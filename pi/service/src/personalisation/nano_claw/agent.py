"""
Nano Claw — cognitive agent.
Claude Haiku with tool use. Handles tasks, notes, emails, research, and retrieval.
Does NOT write in a stylised voice — direct and practical only.

Tools:
  retrieve_context   — search knowledge base (ChromaDB)
  write_note         — save .md to Obsidian vault
  list_recent_notes  — list recent vault entries
  search_web         — DuckDuckGo search (no API key)
  send_email         — SMTP email (requires SMTP config in .env)
"""

from __future__ import annotations

import asyncio
import logging
import re
import smtplib
import sqlite3
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import anthropic

from corpus.embeddings.index import add_documents
from corpus.embeddings.retrieval import retrieve
from corpus.preprocessors import text as text_prep

from ..config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Conversation history — SQLite-backed, persists across restarts
# ---------------------------------------------------------------------------
_MAX_HISTORY_TURNS = 20  # keep last N user/assistant exchanges per user

def _db_path() -> Path:
    return Path(settings.data_dir) / "conversations.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), check_same_thread=False)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS history ("
        "  id      INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  user_id TEXT    NOT NULL,"
        "  role    TEXT    NOT NULL,"
        "  content TEXT    NOT NULL,"
        "  ts      TEXT    NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_history_user ON history(user_id, id)")
    conn.commit()
    return conn


def _load_history(user_id: str) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT role, content FROM history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, _MAX_HISTORY_TURNS * 2),
    ).fetchall()
    conn.close()
    # rows are newest-first; reverse to chronological order
    return [{"role": r, "content": c} for r, c in reversed(rows)]


def _save_history(user_id: str, user_msg: str, assistant_msg: str) -> None:
    conn = _get_conn()
    conn.executemany(
        "INSERT INTO history (user_id, role, content) VALUES (?, ?, ?)",
        [(user_id, "user", user_msg), (user_id, "assistant", assistant_msg)],
    )
    # Prune oldest rows beyond the limit
    conn.execute(
        "DELETE FROM history WHERE user_id = ? AND id NOT IN ("
        "  SELECT id FROM history WHERE user_id = ? ORDER BY id DESC LIMIT ?"
        ")",
        (user_id, user_id, _MAX_HISTORY_TURNS * 2),
    )
    conn.commit()
    conn.close()

def _system_prompt() -> str:
    today = datetime.now().strftime("%A, %d %B %Y")  # e.g. "Monday, 13 April 2026"
    return f"""\
You are a cognitive assistant with access to the user's knowledge base, Obsidian vault, \
email, calendar, and the web.

Today's date is {today}. Use this to resolve relative dates like "tomorrow", "this Thursday", \
"next week", etc. without asking the user to clarify. For vague times like "morning" use 09:00, \
"afternoon" use 14:00, "evening" use 19:00. Always display dates to the user in dd/mm/yy format \
(e.g. 17/05/26). Use ISO 8601 (YYYY-MM-DD) only for tool parameters that require it.

Always respond in English, regardless of the language of the input.

Be direct and practical. Do not use a creative or stylised voice.

Do not preamble. Never say things like "I can see your notes" or "Let me now..." before acting — \
just act. For research and synthesis tasks, be neutral and unbiased: report findings as-is \
without framing them positively or negatively. Skip affirmations, commentary on the quality \
of the user's work, and transitional filler. Lead with the output.

Available capabilities:
- Retrieve context from the user's knowledge base
- Save notes and todos to Obsidian
- List recent vault notes
- Search the web for current information
- Send emails via the configured SMTP account
- Read recent emails from the inbox
- Get upcoming calendar events
- Create new calendar events
- Draft content for emails, messages, or documents (without styling)

Use tools proactively. If the user asks you to save something, save it. \
If they ask about something recent or factual, search the web. \
If they ask you to send an email, draft it clearly and send it. \
If they ask what's on their calendar or what emails they have, use the tools.

Task execution rules:
- If the user gives you a single task that you can resolve immediately (research, lookup, summarise), \
  do it now: use search_web or other tools, then call complete_task with the result. \
  Only use create_task if they explicitly want it saved as a reminder for later.
- If the user gives you multiple tasks at once, save them all first with create_task, \
  then work through each one sequentially using search_web and complete_task.
"""

_TOOLS = [
    {
        "name": "retrieve_context",
        "description": "Search the user's personal knowledge base (Obsidian vault + uploaded content).",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "n_results": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "write_note",
        "description": "Save a note or todo to the Obsidian vault as a markdown file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Note title / filename"},
                "content": {"type": "string", "description": "Markdown body"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional tags"},
            },
            "required": ["title", "content"],
        },
    },
    {
        "name": "list_recent_notes",
        "description": "List recently modified notes from the Obsidian vault.",
        "input_schema": {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "search_web",
        "description": (
            "Search the web for current information. Use for news, facts, research, prices, "
            "anything that may have changed since the model's training cutoff."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "send_email",
        "description": (
            "Send an email using the configured SMTP account. "
            "Compose a clear, professional message and send it. "
            "Always confirm the recipient and subject before sending."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string"},
                "body": {"type": "string", "description": "Plain text email body"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "read_emails",
        "description": "Read recent unread emails from the inbox. Returns subjects, senders, and snippets.",
        "input_schema": {
            "type": "object",
            "properties": {
                "max_emails": {"type": "integer", "default": 10, "description": "Maximum number of emails to fetch"},
                "folders": {"type": "array", "items": {"type": "string"}, "description": "IMAP folders to check (default: INBOX)"},
            },
        },
    },
    {
        "name": "get_calendar_events",
        "description": "Get upcoming calendar events for the next N days.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {"type": "integer", "default": 7, "description": "Number of days ahead to look"},
            },
        },
    },
    {
        "name": "add_calendar_event",
        "description": (
            "Create a new calendar event. Writes to BOTH the Obsidian vault (Full Calendar plugin) "
            "and the CalDAV server (iPhone/Mac Calendar). "
            "For all-day events pass dates like '2026-05-17T00:00:00'. "
            "For timed events use the actual start/end times. "
            "Always pass location and description when you have them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "start_iso": {"type": "string", "description": "Start datetime in ISO 8601 format (e.g. 2026-04-15T14:00:00)"},
                "end_iso": {"type": "string", "description": "End datetime in ISO 8601 format"},
                "location": {"type": "string"},
                "description": {"type": "string", "description": "Extra details to include in the vault note body"},
                "all_day": {"type": "boolean", "description": "True for all-day events (default false)"},
            },
            "required": ["title", "start_iso", "end_iso"],
        },
    },
    {
        "name": "add_book",
        "description": (
            "Search Google Books and add the book to the Obsidian vault. "
            "Places it in the correct genre subfolder under Art/Books/List/ automatically. "
            "Use for 'add book X to my library', '/book <title>', or any book-related request."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Book title and/or author to search for"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "complete_task",
        "description": (
            "Mark an existing task as complete in the Obsidian vault and record the result. "
            "Use this after researching or resolving a task — find the open '- [ ]' line, "
            "tick it, and append the result/link beneath it. "
            "Always call this after search_web when closing out a research task."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Partial text of the task to match (case-insensitive)"},
                "result": {"type": "string", "description": "Result, summary, or URL to record under the task"},
                "note": {"type": "string", "description": "Vault note to update (default: Tasks)"},
            },
            "required": ["task", "result"],
        },
    },
    {
        "name": "create_task",
        "description": (
            "Create a task or reminder in the Obsidian vault using Tasks plugin format. "
            "Use for reminders, todos, and follow-ups. "
            "If the user says 'remind me to X on Friday' or 'add a task to Y', use this tool. "
            "When a due_date is provided, a CalDAV calendar event is also created automatically "
            "so the task appears on the calendar with vault context in the description."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The task description"},
                "due_date": {"type": "string", "description": "Due date in YYYY-MM-DD format (optional)"},
                "due_time": {"type": "string", "description": "Time in HH:MM (24h) format — creates a timed calendar event instead of all-day (optional)"},
                "note": {"type": "string", "description": "Which vault note to append to (default: Tasks)"},
            },
            "required": ["task"],
        },
    },
]


async def respond(user_id: str, message: str) -> str:
    """Run the Haiku tool-use loop and return the final response."""
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    # Seed with prior conversation turns, then append the new user message
    prior = _load_history(user_id)
    messages: list[dict] = prior + [{"role": "user", "content": message}]

    for _ in range(8):  # max 8 tool rounds
        response = await client.messages.create(
            model=settings.nano_claw_model,
            max_tokens=8192,
            system=_system_prompt(),
            tools=_TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            reply = _extract_text(response)
            _save_history(user_id, message, reply)
            return reply

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = await _run_tool(user_id, block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
            continue

        break

    reply = _extract_text(response)
    _save_history(user_id, message, reply)
    return reply


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

async def _run_tool(user_id: str, name: str, inputs: dict) -> str:
    if name == "retrieve_context":
        return _retrieve_context(user_id, inputs["query"], inputs.get("n_results", 5))

    if name == "write_note":
        return _write_note(
            title=inputs["title"],
            content=inputs["content"],
            tags=inputs.get("tags", []),
            user_id=user_id,
        )

    if name == "list_recent_notes":
        return _list_recent_notes(inputs.get("n", 10))

    if name == "search_web":
        return await asyncio.get_event_loop().run_in_executor(
            None, _search_web, inputs["query"], inputs.get("max_results", 5)
        )

    if name == "send_email":
        return await asyncio.get_event_loop().run_in_executor(
            None, _send_email, inputs["to"], inputs["subject"], inputs["body"]
        )

    if name == "read_emails":
        from ..feeds.email_reader import fetch_emails, EmailConfig
        cfg = EmailConfig(
            max_emails=inputs.get("max_emails", 10),
            folders=inputs.get("folders", ["INBOX"]),
        )
        items = await fetch_emails(cfg)
        if not items:
            return "No unread emails found."
        return "\n\n".join(
            f"From: {e.sender}\nSubject: {e.subject}\n{e.snippet[:300]}"
            for e in items
        )

    if name == "get_calendar_events":
        from ..cal.client import get_events
        events = await get_events(days_ahead=inputs.get("days_ahead", 7))
        if not events:
            return "No upcoming events found."
        return "\n".join(
            f"- {e.start.strftime('%d/%m/%y %H:%M')} {e.title}"
            + (f" @ {e.location}" if e.location else "")
            for e in events
        )

    if name == "add_calendar_event":
        from ..cal.client import add_event
        from .. import vault_writer
        from datetime import datetime

        start = datetime.fromisoformat(inputs["start_iso"])
        end = datetime.fromisoformat(inputs["end_iso"])
        all_day = inputs.get("all_day", False)
        location = inputs.get("location", "")
        description = inputs.get("description", "")

        results = []

        # ── 1. Write vault note (Full Calendar) ─────────────────────────────
        if settings.obsidian_vault_path:
            vault = Path(settings.obsidian_vault_path)
            event_date = start.date() if hasattr(start, "date") else start
            start_time = "" if all_day else start.strftime("%H:%M")
            end_time = "" if all_day else end.strftime("%H:%M")
            path, created = vault_writer.write_event(
                vault=vault,
                title=inputs["title"],
                event_date=event_date,
                start_time=start_time,
                end_time=end_time,
                location=location,
                description=description,
                all_day=all_day,
            )
            results.append(f"Vault note {'created' if created else 'already exists'}: Journal/{path.name}")

        # ── 2. Write CalDAV event (iPhone/Mac Calendar) ──────────────────────
        cal_result = await add_event(
            title=inputs["title"],
            start=start,
            end=end,
            location=location,
            description=description,
        )
        results.append(f"Calendar: {cal_result}")

        return "\n".join(results)

    if name == "add_book":
        if not settings.obsidian_vault_path:
            return "Error: OBSIDIAN_VAULT_PATH is not configured."
        from .. import vault_writer
        vault = Path(settings.obsidian_vault_path)
        _, _, summary = vault_writer.write_book(vault, inputs["query"])
        return summary

    if name == "create_task":
        return await _create_task(
            task=inputs["task"],
            due_date=inputs.get("due_date", ""),
            due_time=inputs.get("due_time", ""),
            note=inputs.get("note", "Tasks"),
        )

    if name == "complete_task":
        return _complete_task(
            task=inputs["task"],
            result=inputs["result"],
            note=inputs.get("note", "Tasks"),
        )

    return f"Unknown tool: {name}"


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _retrieve_context(user_id: str, query: str, n_results: int) -> str:
    results = retrieve(user_id, query, n_results=n_results)
    if not results:
        return "No relevant context found."
    return "\n\n".join(f"[{i+1}] {r['text'][:600]}" for i, r in enumerate(results))


def _write_note(title: str, content: str, tags: list[str], user_id: str) -> str:
    if not settings.obsidian_vault_path:
        return "Error: OBSIDIAN_VAULT_PATH is not configured."

    vault = Path(settings.obsidian_vault_path)
    vault.mkdir(parents=True, exist_ok=True)

    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title).strip()
    if not safe_title:
        safe_title = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    frontmatter = f"---\ncreated: {datetime.now().isoformat()}\nsource: nano-claw\n"
    if tags:
        tag_list = ", ".join(f'"{t}"' for t in tags)
        frontmatter += f"tags: [{tag_list}]\n"
    frontmatter += "---\n\n"

    note_path = vault / f"{safe_title}.md"
    note_path.write_text(frontmatter + content, encoding="utf-8")

    docs = text_prep.preprocess(note_path.read_bytes(), note_path.name, source_type="own_writing")
    add_documents(user_id, docs)

    logger.info(f"Note written: {note_path.name}")
    return f"Saved: {note_path.name}"


def _list_recent_notes(n: int) -> str:
    if not settings.obsidian_vault_path:
        return "Error: OBSIDIAN_VAULT_PATH is not configured."

    vault = Path(settings.obsidian_vault_path)
    if not vault.exists():
        return "Vault not found."

    notes = sorted(vault.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:n]
    if not notes:
        return "No notes found."

    return "\n".join(
        f"- {note.stem} ({datetime.fromtimestamp(note.stat().st_mtime).strftime('%d/%m/%y %H:%M')})"
        for note in notes
    )


async def _create_task(task: str, due_date: str, due_time: str, note: str) -> str:
    if not settings.obsidian_vault_path:
        return "Error: OBSIDIAN_VAULT_PATH is not configured."

    vault = Path(settings.obsidian_vault_path)
    if not vault.exists():
        return "Vault not found."

    # Normalise due_date to dd/mm/yy for display; parse ISO if model supplies it
    display_due = ""
    iso_due = due_date
    if due_date:
        try:
            if "/" in due_date:
                _d = datetime.strptime(due_date, "%d/%m/%y").date()
            else:
                _d = datetime.strptime(due_date, "%Y-%m-%d").date()
            display_due = _d.strftime("%d/%m/%y")
            iso_due = _d.strftime("%Y-%m-%d")
        except ValueError:
            display_due = due_date  # leave as-is if unparseable
    due_str = f" 📅 {display_due}" if display_due else ""
    task_line = f"- [ ] {task}{due_str}\n"

    note_path = vault / f"{note}.md"
    if note_path.exists():
        with note_path.open("a", encoding="utf-8") as f:
            f.write(task_line)
    else:
        note_path.write_text(f"# {note}\n\n{task_line}", encoding="utf-8")

    result = f"Task saved to vault: {task}{due_str}"

    # ── Mirror to CalDAV calendar if a due date is provided ──────────────
    if iso_due and settings.caldav_url:
        try:
            from ..cal.client import add_event
            from datetime import timedelta, timezone

            if due_time:
                # Timed event — 1 hour duration by default
                start = datetime.fromisoformat(f"{iso_due}T{due_time}:00")
                end = start + timedelta(hours=1)
            else:
                # All-day event represented as midnight → midnight+1day UTC
                start = datetime.combine(
                    datetime.strptime(iso_due, "%Y-%m-%d").date(),
                    datetime.min.time(),
                    tzinfo=timezone.utc,
                )
                end = start + timedelta(days=1)

            cal_result = await add_event(
                title=task,
                start=start,
                end=end,
                description=f"Vault task — {note}.md",
            )
            result += f"\nCalendar: {cal_result}"
        except Exception as exc:
            logger.warning(f"CalDAV event creation failed for task '{task}': {exc}")
            result += "\nCalendar: not synced (CalDAV unavailable)"

    return result


def _complete_task(task: str, result: str, note: str = "Tasks") -> str:
    if not settings.obsidian_vault_path:
        return "Error: OBSIDIAN_VAULT_PATH is not configured."

    vault = Path(settings.obsidian_vault_path)
    note_path = vault / f"{note}.md"
    if not note_path.exists():
        return f"{note}.md not found in vault."

    lines = note_path.read_text(encoding="utf-8").splitlines()
    matched = False
    new_lines = []
    for line in lines:
        if not matched and re.match(r"^\s*-\s*\[\s*\]", line) and task.lower() in line.lower():
            completed = re.sub(r"\[\s*\]", "[x]", line, count=1)
            new_lines.append(completed)
            new_lines.append(f"  - Result: {result}")
            matched = True
        else:
            new_lines.append(line)

    if not matched:
        return f"No open task matching '{task}' found in {note}.md."

    note_path.write_text("\n".join(new_lines), encoding="utf-8")
    logger.info(f"Task completed: {task}")
    return f"Task marked complete: {task}"


def _search_web(query: str, max_results: int) -> str:
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return "No results found."
        parts = [f"**{r['title']}**\n{r['body']}\n{r['href']}" for r in results]
        return "\n\n".join(parts)
    except Exception as e:
        logger.error(f"Web search failed: {e}")
        return f"Search failed: {e}"


def _send_email(to: str, subject: str, body: str) -> str:
    if not all([settings.smtp_host, settings.smtp_user, settings.smtp_password]):
        return (
            "Email not configured. Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD in service/.env. "
            "For Gmail, use an App Password from myaccount.google.com/apppasswords."
        )

    msg = EmailMessage()
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
            smtp.starttls()
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
        logger.info(f"Email sent to {to}: {subject}")
        return f"Email sent to {to}."
    except Exception as e:
        logger.error(f"Email send failed: {e}")
        return f"Failed to send email: {e}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_text(response) -> str:
    for block in response.content:
        if hasattr(block, "text"):
            return block.text
    return ""
