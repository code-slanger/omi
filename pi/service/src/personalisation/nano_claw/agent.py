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
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import anthropic

from ..config import settings
from ..embeddings.index import add_documents
from ..embeddings.retrieval import retrieve
from ..preprocessors import text as text_prep

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a cognitive assistant with access to the user's knowledge base, Obsidian vault, \
email, calendar, and the web.

Always respond in English, regardless of the language of the input.

Be direct and practical. Do not use a creative or stylised voice.

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
        "description": "Create a new calendar event.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "start_iso": {"type": "string", "description": "Start datetime in ISO 8601 format (e.g. 2026-04-15T14:00:00)"},
                "end_iso": {"type": "string", "description": "End datetime in ISO 8601 format"},
                "location": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["title", "start_iso", "end_iso"],
        },
    },
]


async def respond(user_id: str, message: str) -> str:
    """Run the Haiku tool-use loop and return the final response."""
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    messages: list[dict] = [{"role": "user", "content": message}]

    for _ in range(8):  # max 8 tool rounds
        response = await client.messages.create(
            model=settings.nano_claw_model,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=_TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            return _extract_text(response)

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

    return _extract_text(response)


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
            f"- {e.start.strftime('%Y-%m-%d %H:%M')} {e.title}"
            + (f" @ {e.location}" if e.location else "")
            for e in events
        )

    if name == "add_calendar_event":
        from ..cal.client import add_event
        from datetime import datetime
        start = datetime.fromisoformat(inputs["start_iso"])
        end = datetime.fromisoformat(inputs["end_iso"])
        return await add_event(
            title=inputs["title"],
            start=start,
            end=end,
            location=inputs.get("location", ""),
            description=inputs.get("description", ""),
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
        f"- {note.stem} ({datetime.fromtimestamp(note.stat().st_mtime).strftime('%Y-%m-%d %H:%M')})"
        for note in notes
    )


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
