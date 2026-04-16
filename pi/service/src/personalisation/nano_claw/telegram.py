"""
Telegram bot — mobile interface for both creative and cognitive workflows.

Accepts text, voice notes, photos, and videos. Routes to the appropriate agent.
All messages are associated with settings.nano_claw_user_id.

Commands:
  /create <prompt>   — force creative mode (writer agent)
  /write <prompt>    — alias for /create
  /note <content>    — save note to Obsidian (cognitive)
  /todo <task>       — create task note (cognitive)
  /email <details>   — draft an email (cognitive)
  /context <query>   — raw knowledge base search
  /status            — show system status
  /help              — show this help

Free-form messages are auto-classified as creative or cognitive.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..config import settings
from ..transcription import transcribe_file
from . import router

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Conversation states
# ---------------------------------------------------------------------------

# /event conversation
EVENT_TITLE, EVENT_DATE, EVENT_TIME, EVENT_LOCATION = range(4)

# /task conversation
TASK_DESC, TASK_LIST, TASK_DATE, TASK_TIME = range(4, 8)

# /book conversation
BOOK_QUERY = 8

# /daily conversation
DAILY_MOOD, DAILY_HABITS, DAILY_GRATEFUL, DAILY_HIGHLIGHT = range(9, 13)

# /weekly conversation
WEEKLY_DATE, WEEKLY_GOAL_1, WEEKLY_GOAL_2, WEEKLY_GOAL_3 = range(13, 17)


async def _transcribe(file_path: str) -> str:
    return await transcribe_file(file_path)


def _parse_date(s: str) -> date:
    """Accept dd/mm/yy or YYYY-MM-DD and return a date object."""
    if "/" in s:
        return datetime.strptime(s, "%d/%m/%y").date()
    return date.fromisoformat(s)


def _split_message(text: str, limit: int = 4000) -> list[str]:
    """Split long text into Telegram-safe chunks."""
    if len(text) <= limit:
        return [text]
    parts = []
    while text:
        parts.append(text[:limit])
        text = text[limit:]
    return parts


async def _reply(update: Update, text: str) -> None:
    for part in _split_message(text):
        await update.message.reply_text(part)


def _strip_for_telegram(content: str) -> str:
    """Strip frontmatter and plugin code blocks so the note reads well in Telegram."""
    # Remove frontmatter
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            content = parts[2].lstrip("\n")

    # Remove dataview / todoist / dataviewjs fenced blocks
    content = re.sub(r"```(?:dataview|todoist|dataviewjs).*?```", "", content, flags=re.DOTALL)

    # Remove ![[...]] transclusions
    content = re.sub(r"!\[\[.*?\]\]", "", content)

    # Collapse multiple blank lines
    content = re.sub(r"\n{3,}", "\n\n", content)

    return content.strip()


# ---------------------------------------------------------------------------
# Free-form message handlers
# ---------------------------------------------------------------------------

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if not text:
        return
    await update.message.reply_text("thinking...")
    try:
        response, mode = await router.route(settings.nano_claw_user_id, text)
        await _reply(update, response)
    except Exception as e:
        logger.error(f"Text handler error: {e}")
        await update.message.reply_text(f"Error: {e}")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("transcribing...")
    voice = update.message.voice or update.message.audio
    tg_file = await context.bot.get_file(voice.file_id)

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        await tg_file.download_to_drive(tmp.name)
        tmp_path = tmp.name

    try:
        transcript = await _transcribe(tmp_path)
    finally:
        os.unlink(tmp_path)

    if not transcript:
        await update.message.reply_text("Could not transcribe audio.")
        return

    caption = (update.message.caption or "").strip()
    response, mode = await router.route(settings.nano_claw_user_id, caption, media_context=transcript)

    prefix = f"_{transcript}_\n\n" if mode == "creative" else f"Transcript: {transcript}\n\n"
    await _reply(update, prefix + response)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    photo = update.message.photo[-1]
    tg_file = await context.bot.get_file(photo.file_id)
    caption = (update.message.caption or "").strip()

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        await tg_file.download_to_drive(tmp.name)
        tmp_path = tmp.name

    try:
        saved_path = _save_photo_to_vault(tmp_path)
        media_context = f"Photo saved to vault: {saved_path.name}" if saved_path else "Photo received."
    finally:
        if Path(tmp_path).exists():
            os.unlink(tmp_path)

    prompt = caption or "I just sent a photo"
    response, _ = await router.route(settings.nano_claw_user_id, prompt, media_context=media_context)
    await _reply(update, response)


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("processing video...")
    video = update.message.video or update.message.video_note
    tg_file = await context.bot.get_file(video.file_id)

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        await tg_file.download_to_drive(tmp.name)
        video_path = tmp.name

    audio_path = video_path + ".wav"
    try:
        subprocess.run(
            ["ffmpeg", "-i", video_path, "-vn", "-ar", "16000", "-ac", "1", audio_path, "-y"],
            capture_output=True,
            check=True,
        )
        transcript = await _transcribe(audio_path)
        caption = (update.message.caption or "").strip()
        response, mode = await router.route(settings.nano_claw_user_id, caption, media_context=transcript)
        prefix = f"_{transcript}_\n\n" if transcript else ""
        await _reply(update, prefix + response)
    except subprocess.CalledProcessError:
        await update.message.reply_text("Could not extract audio from video.")
    except Exception as e:
        logger.error(f"Video handler error: {e}")
        await update.message.reply_text(f"Error: {e}")
    finally:
        os.unlink(video_path)
        if Path(audio_path).exists():
            os.unlink(audio_path)


def _save_photo_to_vault(src_path: str) -> Path | None:
    if not settings.obsidian_vault_path:
        return None
    vault = Path(settings.obsidian_vault_path)
    attachments = vault / "attachments"
    attachments.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = attachments / f"telegram_{ts}.jpg"
    Path(src_path).rename(dest)
    return dest


# ---------------------------------------------------------------------------
# Slash command handlers — simple (no conversation)
# ---------------------------------------------------------------------------

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "*Nano Claw*\n\n"
        "Messages are automatically classified — just talk naturally.\n\n"

        "*Creative (Alchemist OS — writes in your voice):*\n"
        "`/create <prompt>` or `/write <prompt>`\n\n"

        "*Calendar & Events (vault note + CalDAV → iPhone/Mac):*\n"
        "`/event` — guided multi-step event creation\n"
        "`/event Birmingham Park Run on 17th May` — inline shortcut\n\n"

        "*Tasks (vault + CalDAV reminder):*\n"
        "`/task` — guided multi-step task creation\n"
        "`/task Call dentist due:15/04/26` — inline shortcut\n\n"

        "*Books (Google Books → vault genre folder):*\n"
        "`/book` — guided book search\n"
        "`/book Atomic Habits James Clear` — inline shortcut\n\n"

        "*Journal (guided — fills in mood, habits, reflection):*\n"
        "`/daily [date]` — create daily note via conversation\n"
        "`/weekly [date]` — create weekly note + set goals\n"
        "`/monthly [YYYY-MM]` — create monthly note (instant)\n\n"

        "*Read journal notes:*\n"
        "`/getdaily [date]` — read daily note (stripped for readability)\n"
        "`/getweekly [date]` — read weekly note\n\n"

        "*Task lists (direct file read/write — instant):*\n"
        "`/tasks` — menu of all lists with open task counts\n"
        "`/tasks food-shop` — show list with ✅ buttons to complete\n"
        "`/add inbox Call the dentist` — append task to a list\n"
        f"Lists: inbox, locus, personal, food-shop, health, finance, business, hyrox\n\n"

        "*Quick reads (no AI — instant):*\n"
        "`/todos` — open tasks from Tasks.md\n"
        "`/shopping` — shopping list from Shopping.md\n\n"

        "*Cognitive (Nano Claw — practical tasks):*\n"
        "`/note <content>` — save note to Obsidian\n"
        "`/research <topic>` — web search + synthesis\n"
        "`/email <to> | <subject> | <body>` — send an email\n"
        "`/draft <details>` — draft a message or email (no send)\n"
        "`/context <query>` — search your knowledge base\n\n"

        "*Productivity:*\n"
        "`/digest [date]` — link to today's daily digest\n\n"

        "*Media (auto-routed):*\n"
        "🎤 Voice note → transcribed → classified\n"
        "📷 Photo → saved to vault attachments\n"
        "🎬 Video → audio extracted → transcribed → classified\n\n"

        "`/cancel` — cancel any active conversation\n"
        "`/status` — system info"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_create(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    prompt = " ".join(context.args) if context.args else ""
    if not prompt:
        await update.message.reply_text("Usage: /create <prompt>")
        return
    await update.message.reply_text("writing...")
    from ..agents.writer import generate
    result = await generate(settings.nano_claw_user_id, prompt)
    await _reply(update, result)


async def cmd_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    content = " ".join(context.args) if context.args else ""
    if not content:
        await update.message.reply_text("Usage: /note <content>")
        return
    from . import agent
    result = await agent.respond(settings.nano_claw_user_id, f"Save this as a note: {content}")
    await _reply(update, result)


async def cmd_todo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    task = " ".join(context.args) if context.args else ""
    if not task:
        await update.message.reply_text("Usage: /todo <task>")
        return
    from . import agent
    result = await agent.respond(settings.nano_claw_user_id, f"Create a todo note: {task}")
    await _reply(update, result)


async def cmd_todos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List open tasks from Tasks.md — no AI."""
    from .commands import run as run_command
    await _reply(update, run_command("todos"))


async def cmd_shopping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List shopping list from Shopping.md — no AI."""
    from .commands import run as run_command
    await _reply(update, run_command("shopping"))


async def cmd_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    details = " ".join(context.args) if context.args else ""
    if not details:
        await update.message.reply_text(
            "Usage:\n"
            "`/email to@addr.com | Subject | Body`\n"
            "or describe it: `/email john@x.com about the project update`",
            parse_mode="Markdown",
        )
        return
    await update.message.reply_text("composing and sending...")
    from . import agent
    result = await agent.respond(settings.nano_claw_user_id, f"Send an email: {details}")
    await _reply(update, result)


async def cmd_research(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    topic = " ".join(context.args) if context.args else ""
    if not topic:
        await update.message.reply_text("Usage: /research <topic>")
        return
    await update.message.reply_text("searching...")
    from . import agent
    result = await agent.respond(
        settings.nano_claw_user_id,
        f"Research this topic and give me a clear summary: {topic}"
    )
    await _reply(update, result)


async def cmd_draft(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    details = " ".join(context.args) if context.args else ""
    if not details:
        await update.message.reply_text("Usage: /draft <what to write>")
        return
    await update.message.reply_text("drafting...")
    from . import agent
    result = await agent.respond(
        settings.nano_claw_user_id,
        f"Draft this without sending: {details}"
    )
    await _reply(update, result)


async def cmd_context(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args) if context.args else ""
    if not query:
        await update.message.reply_text("Usage: /context <query>")
        return
    from corpus.embeddings.retrieval import retrieve
    results = retrieve(settings.nano_claw_user_id, query, n_results=5)
    if not results:
        await update.message.reply_text("No relevant context found.")
        return
    parts = [f"*{i+1}.* {r['text'][:400]}" for i, r in enumerate(results)]
    await _reply(update, "\n\n".join(parts))


async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    date_str = (context.args[0] if context.args else None) or date.today().isoformat()
    base = settings.digest_base_url.rstrip("/") or "http://192.168.0.27:8000"
    url = f"{base}/digest/{date_str}"
    await update.message.reply_text(
        f"[Daily Digest — {date_str}]({url})\n\nOr build on demand: `POST /digest/build`",
        parse_mode="Markdown",
        disable_web_page_preview=False,
    )


async def cmd_daily(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    vault_path = settings.obsidian_vault_path
    if not vault_path:
        await update.message.reply_text("OBSIDIAN_VAULT_PATH is not configured.")
        return

    target = date.today()
    if context.args:
        try:
            target = _parse_date(context.args[0])
        except ValueError:
            await update.message.reply_text("Usage: /daily [DD/MM/YY]")
            return

    from .. import vault_writer
    path, created = vault_writer.write_daily(Path(vault_path), target)
    status = "Created" if created else "Already exists"
    await update.message.reply_text(
        f"{status}: `Journal/Daily/{path.name}`",
        parse_mode="Markdown",
    )


async def cmd_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    vault_path = settings.obsidian_vault_path
    if not vault_path:
        await update.message.reply_text("OBSIDIAN_VAULT_PATH is not configured.")
        return

    target = date.today()
    if context.args:
        arg = context.args[0]
        try:
            if "W" in arg.upper():
                year, wk = arg.upper().replace("W", "-W").split("-W")
                jan4 = date(int(year), 1, 4)
                monday = jan4 - timedelta(days=jan4.isocalendar()[2] - 1)
                monday += timedelta(weeks=int(wk) - 1)
                target = monday
            else:
                target = _parse_date(arg)
        except (ValueError, AttributeError):
            await update.message.reply_text("Usage: /weekly [DD/MM/YY or YYYY-Www]")
            return

    from .. import vault_writer
    monday = vault_writer._monday_of_week(target)
    path, created = vault_writer.write_weekly(Path(vault_path), monday)
    status = "Created" if created else "Already exists"
    await update.message.reply_text(
        f"{status}: `Journal/Weekly/{path.name}`",
        parse_mode="Markdown",
    )


async def cmd_monthly(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    vault_path = settings.obsidian_vault_path
    if not vault_path:
        await update.message.reply_text("OBSIDIAN_VAULT_PATH is not configured.")
        return

    target = date.today()
    if context.args:
        arg = context.args[0]
        try:
            if len(arg) == 7:
                target = date.fromisoformat(arg + "-01")
            else:
                target = date.fromisoformat(arg)
        except ValueError:
            await update.message.reply_text("Usage: /monthly [YYYY-MM]")
            return

    from .. import vault_writer
    path, created = vault_writer.write_monthly(Path(vault_path), target)
    status = "Created" if created else "Already exists"
    await update.message.reply_text(
        f"{status}: `Journal/Monthly/{path.name}`",
        parse_mode="Markdown",
    )


async def cmd_getdaily(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Read today's (or a specific) daily note from the vault, stripped for readability."""
    vault_path = settings.obsidian_vault_path
    if not vault_path:
        await update.message.reply_text("OBSIDIAN_VAULT_PATH is not configured.")
        return

    target = date.today()
    if context.args:
        try:
            target = _parse_date(context.args[0])
        except ValueError:
            await update.message.reply_text("Usage: /getdaily [DD/MM/YY]")
            return

    note_path = Path(vault_path) / "Journal" / "Daily" / f"{target.strftime('%Y-%m-%d')}.md"
    if not note_path.exists():
        await update.message.reply_text(
            f"No daily note for {target}. Create it with /daily."
        )
        return

    content = _strip_for_telegram(note_path.read_text(encoding="utf-8"))
    await _reply(update, content or "(empty note)")


async def cmd_getweekly(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Read this week's (or a specific) weekly note from the vault, stripped for readability."""
    vault_path = settings.obsidian_vault_path
    if not vault_path:
        await update.message.reply_text("OBSIDIAN_VAULT_PATH is not configured.")
        return

    target = date.today()
    if context.args:
        arg = context.args[0]
        try:
            if "W" in arg.upper():
                year, wk = arg.upper().replace("W", "-W").split("-W")
                jan4 = date(int(year), 1, 4)
                monday = jan4 - timedelta(days=jan4.isocalendar()[2] - 1)
                monday += timedelta(weeks=int(wk) - 1)
                target = monday
            else:
                target = _parse_date(arg)
        except (ValueError, AttributeError):
            await update.message.reply_text("Usage: /getweekly [DD/MM/YY or YYYY-Www]")
            return

    from .. import vault_writer
    monday = vault_writer._monday_of_week(target)
    iso_year = monday.isocalendar()[0]
    week_num = monday.isocalendar()[1]
    week_str = f"{iso_year}-W{week_num:02d}"

    note_path = Path(vault_path) / "Journal" / "Weekly" / f"{week_str}.md"
    if not note_path.exists():
        await update.message.reply_text(
            f"No weekly note for {week_str}. Create it with /weekly."
        )
        return

    content = _strip_for_telegram(note_path.read_text(encoding="utf-8"))
    await _reply(update, content or "(empty note)")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from corpus.embeddings.index import collection_count
    count = collection_count(settings.nano_claw_user_id)
    vault = settings.obsidian_vault_path
    vault_ok = Path(vault).exists() if vault else False
    chat_id = update.effective_chat.id if update.effective_chat else "unknown"
    text = (
        f"*Status*\n"
        f"User: `{settings.nano_claw_user_id}`\n"
        f"Chat ID: `{chat_id}` ← set as TELEGRAM\\_CHAT\\_ID for digest\n"
        f"Corpus: {count} chunks\n"
        f"Vault: {'✓ ' + vault if vault_ok else '✗ not configured'}\n"
        f"Agent model: `{settings.nano_claw_model}`\n"
        f"Writer model: `{settings.generation_model}`\n"
        f"Digest: /digest/today at {settings.digest_base_url or 'http://192.168.0.27:8000'}\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ---------------------------------------------------------------------------
# ConversationHandler — /event
# ---------------------------------------------------------------------------
# Flow: title → date → time (All day / HH:MM) → location (Skip / text) → done

_SKIP_KB = ReplyKeyboardMarkup([["Skip"]], one_time_keyboard=True, resize_keyboard=True)
_ALLDAY_KB = ReplyKeyboardMarkup(
    [["All day", "Skip"]], one_time_keyboard=True, resize_keyboard=True
)
_NODATE_KB = ReplyKeyboardMarkup(
    [["No date"]], one_time_keyboard=True, resize_keyboard=True
)


async def event_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for /event — if args given skip straight to date question."""
    inline = " ".join(context.args) if context.args else ""
    if inline:
        context.user_data["event_title"] = inline
        await update.message.reply_text(
            f"*{inline}*\n\n📅 What date? (e.g. 17/05/26, next Friday, 17th May)",
            parse_mode="Markdown",
        )
        return EVENT_DATE

    await update.message.reply_text("📅 What's the event called?")
    return EVENT_TITLE


async def event_get_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["event_title"] = update.message.text.strip()
    await update.message.reply_text(
        f"*{context.user_data['event_title']}*\n\n📅 What date? (e.g. 17/05/26, next Friday, 17th May)",
        parse_mode="Markdown",
    )
    return EVENT_DATE


async def event_get_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["event_date"] = update.message.text.strip()
    await update.message.reply_text(
        "⏰ What time? Type HH:MM or choose:",
        reply_markup=_ALLDAY_KB,
    )
    return EVENT_TIME


async def event_get_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text.lower() in ("all day", "skip"):
        context.user_data["event_time"] = ""
        context.user_data["event_all_day"] = True
    else:
        context.user_data["event_time"] = text
        context.user_data["event_all_day"] = False

    await update.message.reply_text(
        "📍 Where is it? (optional)",
        reply_markup=_SKIP_KB,
    )
    return EVENT_LOCATION


async def event_get_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    location = "" if text.lower() == "skip" else text

    title = context.user_data.get("event_title", "")
    date_str = context.user_data.get("event_date", "")
    time_str = context.user_data.get("event_time", "")
    all_day = context.user_data.get("event_all_day", True)

    await update.message.reply_text(
        "Adding to calendar and vault...",
        reply_markup=ReplyKeyboardRemove(),
    )

    from . import agent
    prompt = (
        f"Create a calendar event with these details:\n"
        f"Title: {title}\n"
        f"Date: {date_str}\n"
        f"Time: {time_str if time_str else 'all day'}\n"
        f"Location: {location}\n\n"
        "Use the add_calendar_event tool. "
        f"Set all_day={'true' if all_day else 'false'}. "
        "Confirm what was created."
    )
    result = await agent.respond(settings.nano_claw_user_id, prompt)
    await _reply(update, result)
    context.user_data.clear()
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# ConversationHandler — /task
# ---------------------------------------------------------------------------
# Flow: description → list picker → due date (No date / text) → time (Skip / HH:MM) → done

def _list_keyboard() -> ReplyKeyboardMarkup:
    """Build a keyboard from TASK_LISTS keys, 3 per row."""
    keys = list(TASK_LISTS.keys())
    rows = [keys[i:i+3] for i in range(0, len(keys), 3)]
    return ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True)


async def task_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for /task — if args given skip straight to list picker."""
    inline = " ".join(context.args) if context.args else ""
    if inline:
        context.user_data["task_desc"] = inline
        await update.message.reply_text(
            f"*{inline}*\n\n📂 Which list?",
            parse_mode="Markdown",
            reply_markup=_list_keyboard(),
        )
        return TASK_LIST

    await update.message.reply_text("📝 What's the task?")
    return TASK_DESC


async def task_get_desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["task_desc"] = update.message.text.strip()
    await update.message.reply_text(
        f"*{context.user_data['task_desc']}*\n\n📂 Which list?",
        parse_mode="Markdown",
        reply_markup=_list_keyboard(),
    )
    return TASK_LIST


_CONFIRM_KB = ReplyKeyboardMarkup([["Create it", "Cancel"]], one_time_keyboard=True, resize_keyboard=True)


async def task_get_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chosen = update.message.text.strip().lower()

    # Handle confirmation of new list creation
    if context.user_data.get("pending_new_list"):
        pending = context.user_data.pop("pending_new_list")
        if chosen in ("create it", "yes", "y"):
            TASK_LISTS[pending] = f"tasks/{pending}.md"
            context.user_data["task_list"] = pending
            await update.message.reply_text(
                f"📅 When is it due? (e.g. 20/04/26, next Friday)",
                reply_markup=_NODATE_KB,
            )
            return TASK_DATE
        else:
            await update.message.reply_text("Pick an existing list:", reply_markup=_list_keyboard())
            return TASK_LIST

    if chosen not in TASK_LISTS:
        context.user_data["pending_new_list"] = chosen
        await update.message.reply_text(
            f"'{chosen}' doesn't exist. Create it?",
            reply_markup=_CONFIRM_KB,
        )
        return TASK_LIST

    context.user_data["task_list"] = chosen
    await update.message.reply_text(
        f"📅 When is it due? (e.g. 20/04/26, next Friday)",
        reply_markup=_NODATE_KB,
    )
    return TASK_DATE


async def task_get_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text.lower() == "no date":
        desc = context.user_data.get("task_desc", "")
        note_key = context.user_data.get("task_list", "inbox")
        note_path = TASK_LISTS.get(note_key, "tasks/inbox.md").rsplit(".md", 1)[0]
        await update.message.reply_text("Saving task...", reply_markup=ReplyKeyboardRemove())
        from .agent import _create_task
        result = await _create_task(task=desc, due_date="", due_time="", note=note_path)
        await _reply(update, result)
        context.user_data.clear()
        return ConversationHandler.END

    context.user_data["task_date"] = text
    await update.message.reply_text(
        "⏰ What time? (e.g. 14:00)",
        reply_markup=_SKIP_KB,
    )
    return TASK_TIME


async def _resolve_date(raw: str) -> str:
    """Resolve a natural-language date string to YYYY-MM-DD using the LLM."""
    import anthropic
    from ..config import settings
    today = datetime.now().strftime("%A, %d %B %Y")
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    resp = await client.messages.create(
        model=settings.nano_claw_model,
        max_tokens=20,
        system=f"Today is {today}. Reply with only a date in YYYY-MM-DD format. No other text.",
        messages=[{"role": "user", "content": raw}],
    )
    return resp.content[0].text.strip()


async def task_get_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    time_str = "" if text.lower() == "skip" else text

    desc = context.user_data.get("task_desc", "")
    raw_date = context.user_data.get("task_date", "")
    note_key = context.user_data.get("task_list", "inbox")
    note_path = TASK_LISTS.get(note_key, "tasks/inbox.md").rsplit(".md", 1)[0]

    await update.message.reply_text("Saving task...", reply_markup=ReplyKeyboardRemove())

    # Resolve natural-language date to ISO before passing to _create_task
    due_date = ""
    if raw_date:
        try:
            due_date = await _resolve_date(raw_date)
        except Exception:
            due_date = raw_date  # fall back to raw string

    from .agent import _create_task
    result = await _create_task(task=desc, due_date=due_date, due_time=time_str, note=note_path)
    await _reply(update, result)
    context.user_data.clear()
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# ConversationHandler — /book
# ---------------------------------------------------------------------------
# Flow: query (if no args) → search → done

async def book_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for /book — if args given search immediately."""
    inline = " ".join(context.args) if context.args else ""
    if inline:
        await update.message.reply_text(f"Searching Google Books for _{inline}_...", parse_mode="Markdown")
        return await _book_search(update, inline)

    await update.message.reply_text("📚 What book do you want to add? (title, or title + author)")
    return BOOK_QUERY


async def book_get_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.message.text.strip()
    await update.message.reply_text(f"Searching Google Books for _{query}_...", parse_mode="Markdown")
    return await _book_search(update, query)


async def _book_search(update: Update, query: str) -> int:
    vault_path = settings.obsidian_vault_path
    if not vault_path:
        await update.message.reply_text("OBSIDIAN_VAULT_PATH is not configured.")
        return ConversationHandler.END

    from .. import vault_writer
    _, _, summary = vault_writer.write_book(Path(vault_path), query)
    await _reply(update, summary)
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# ConversationHandler — /daily
# ---------------------------------------------------------------------------
# Flow: mood → habits (meditation/exercise/read) → grateful → highlight → create note

_MOOD_KB = ReplyKeyboardMarkup(
    [["😊 Great", "😐 Okay", "😔 Rough"], ["Skip"]],
    one_time_keyboard=True,
    resize_keyboard=True,
)
_HABITS_KB = ReplyKeyboardMarkup(
    [["🧘 Meditate", "🏃 Exercise", "📚 Read"],
     ["✅ All three", "❌ None", "Skip"]],
    one_time_keyboard=True,
    resize_keyboard=True,
)


def _parse_habits(text: str) -> tuple[bool | None, bool | None, bool | None]:
    """Return (meditation, exercise, read) booleans from free-form text."""
    t = text.lower()
    if t in ("❌ none", "none", "nothing", "no"):
        return False, False, False
    if t in ("✅ all three", "all three", "all", "everything"):
        return True, True, True
    if t in ("skip",):
        return None, None, None
    med = any(w in t for w in ("meditat", "🧘", "yoga"))
    ex = any(w in t for w in ("exercis", "gym", "run", "workout", "🏃", "walk", "train"))
    rd = any(w in t for w in ("read", "book", "📚"))
    return med or None, ex or None, rd or None


async def daily_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry for /daily — store optional date arg then ask for mood."""
    target = date.today()
    if context.args:
        try:
            target = _parse_date(context.args[0])
        except ValueError:
            await update.message.reply_text("Unrecognised date — using today.")
    context.user_data["daily_date"] = target

    weekday = target.strftime("%A")
    date_label = target.strftime("%d/%m/%y")
    await update.message.reply_text(
        f"*{weekday}, {date_label}* — let's fill in your daily note.\n\n"
        "🌄 How's your mood?",
        parse_mode="Markdown",
        reply_markup=_MOOD_KB,
    )
    return DAILY_MOOD


async def daily_get_mood(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    # Normalise quick-reply buttons to plain text
    mood_map = {"😊 great": "😊", "😐 okay": "😐", "😔 rough": "😔", "skip": ""}
    context.user_data["daily_mood"] = mood_map.get(text.lower(), text if text.lower() != "skip" else "")
    await update.message.reply_text(
        "Which habits did you do today?",
        reply_markup=_HABITS_KB,
    )
    return DAILY_HABITS


async def daily_get_habits(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    med, ex, rd = _parse_habits(update.message.text.strip())
    context.user_data["daily_meditation"] = med
    context.user_data["daily_exercise"] = ex
    context.user_data["daily_read"] = rd
    await update.message.reply_text(
        "🙏 What are you grateful for today?",
        reply_markup=_SKIP_KB,
    )
    return DAILY_GRATEFUL


async def daily_get_grateful(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    context.user_data["daily_grateful"] = "" if text.lower() == "skip" else text
    await update.message.reply_text(
        "✨ What was the highlight of your day?",
        reply_markup=_SKIP_KB,
    )
    return DAILY_HIGHLIGHT


async def daily_get_highlight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    highlight = "" if text.lower() == "skip" else text

    target: date = context.user_data.get("daily_date", date.today())
    vault_path = settings.obsidian_vault_path
    if not vault_path:
        await update.message.reply_text(
            "OBSIDIAN_VAULT_PATH is not configured.",
            reply_markup=ReplyKeyboardRemove(),
        )
        context.user_data.clear()
        return ConversationHandler.END

    from .. import vault_writer
    path, created = vault_writer.write_daily(
        Path(vault_path),
        target,
        overwrite=False,
        mood=context.user_data.get("daily_mood", ""),
        meditation=context.user_data.get("daily_meditation"),
        exercise=context.user_data.get("daily_exercise"),
        read=context.user_data.get("daily_read"),
        grateful=context.user_data.get("daily_grateful", ""),
        highlight=highlight,
    )

    status = "Created" if created else "Already exists — not overwritten"
    habits_str = ", ".join(
        label
        for label, val in [("🧘", context.user_data.get("daily_meditation")),
                            ("🏃", context.user_data.get("daily_exercise")),
                            ("📚", context.user_data.get("daily_read"))]
        if val
    ) or "none logged"

    await update.message.reply_text(
        f"{status}: `{path.name}`\n"
        f"Mood: {context.user_data.get('daily_mood') or '—'} · Habits: {habits_str}",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    context.user_data.clear()
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# ConversationHandler — /weekly
# ---------------------------------------------------------------------------
# Flow: goal 1 → goal 2 → goal 3 → create note

_DONE_KB = ReplyKeyboardMarkup([["Done / Skip"]], one_time_keyboard=True, resize_keyboard=True)


async def weekly_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry for /weekly — store optional date arg then ask for goals."""
    target = date.today()
    if context.args:
        arg = context.args[0]
        try:
            if "W" in arg.upper():
                year, wk = arg.upper().replace("W", "-W").split("-W")
                jan4 = date(int(year), 1, 4)
                monday = jan4 - timedelta(days=jan4.isocalendar()[2] - 1)
                monday += timedelta(weeks=int(wk) - 1)
                target = monday
            else:
                target = _parse_date(arg)
        except (ValueError, AttributeError):
            await update.message.reply_text("Unrecognised date — using this week.")

    from .. import vault_writer
    monday = vault_writer._monday_of_week(target)
    context.user_data["weekly_monday"] = monday

    week_label = f"W{monday.isocalendar()[1]:02d} {monday.strftime('%d/%m/%y')} – {(monday + timedelta(days=6)).strftime('%d/%m/%y')}"
    await update.message.reply_text(
        f"*{week_label}* — what's goal 1 for this week?",
        parse_mode="Markdown",
        reply_markup=_DONE_KB,
    )
    return WEEKLY_GOAL_1


async def weekly_goal_1(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text.lower() not in ("done / skip", "done", "skip"):
        context.user_data["weekly_goal_1"] = text
    await update.message.reply_text("Goal 2? (or Done / Skip)", reply_markup=_DONE_KB)
    return WEEKLY_GOAL_2


async def weekly_goal_2(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text.lower() not in ("done / skip", "done", "skip"):
        context.user_data["weekly_goal_2"] = text
    await update.message.reply_text("Goal 3? (or Done / Skip)", reply_markup=_DONE_KB)
    return WEEKLY_GOAL_3


async def weekly_goal_3(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text.lower() not in ("done / skip", "done", "skip"):
        context.user_data["weekly_goal_3"] = text

    goals = [
        context.user_data.get("weekly_goal_1", ""),
        context.user_data.get("weekly_goal_2", ""),
        context.user_data.get("weekly_goal_3", ""),
    ]
    # Drop trailing empty goals
    while goals and not goals[-1]:
        goals.pop()

    monday: date = context.user_data.get("weekly_monday", date.today())
    vault_path = settings.obsidian_vault_path
    if not vault_path:
        await update.message.reply_text(
            "OBSIDIAN_VAULT_PATH is not configured.",
            reply_markup=ReplyKeyboardRemove(),
        )
        context.user_data.clear()
        return ConversationHandler.END

    from .. import vault_writer
    path, created = vault_writer.write_weekly(
        Path(vault_path),
        monday,
        goals=goals if goals else None,
    )
    status = "Created" if created else "Already exists — not overwritten"
    goals_summary = "\n".join(f"• {g}" for g in goals) if goals else "no goals set"
    await update.message.reply_text(
        f"{status}: `{path.name}`\n{goals_summary}",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    context.user_data.clear()
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Task list commands — /tasks, /add, inline ✅ buttons
# ---------------------------------------------------------------------------

# Short name → vault-relative path (all within tasks/ folder)
TASK_LISTS: dict[str, str] = {
    "inbox":     "tasks/inbox.md",
    "locus":     "tasks/locus.md",
    "personal":  "tasks/personal.md",
    "food-shop": "tasks/food-shop.md",
    "health":    "tasks/health.md",
    "finance":   "tasks/finance.md",
    "business":  "tasks/business.md",
    "hyrox":     "tasks/hyrox.md",
}


def _read_open_tasks(vault: Path, list_name: str) -> list[tuple[int, str]]:
    """Return [(line_index, task_text), ...] for all open tasks in a list file."""
    rel = TASK_LISTS.get(list_name)
    if not rel:
        return []
    path = vault / rel
    if not path.exists():
        return []
    results = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        m = re.match(r"^- \[ \] (.+)$", line)
        if m:
            results.append((i, m.group(1)))
    return results


def _complete_task(vault: Path, list_name: str, line_idx: int) -> str | None:
    """
    Mark the task at `line_idx` as complete.
    Returns the task text on success, None if not found.
    """
    rel = TASK_LISTS.get(list_name)
    if not rel:
        return None
    path = vault / rel
    if not path.exists():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    if 0 <= line_idx < len(lines) and re.match(r"^- \[ \]", lines[line_idx]):
        task_text = re.sub(r"^- \[ \] ", "", lines[line_idx])
        lines[line_idx] = re.sub(r"^- \[ \]", "- [x]", lines[line_idx])
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return task_text
    return None


def _append_task(vault: Path, list_name: str, text: str) -> bool:
    """Append a new open task to a list file. Returns True on success."""
    rel = TASK_LISTS.get(list_name)
    if not rel:
        return False
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(f"# {list_name.title()}\n\n", encoding="utf-8")
    content = path.read_text(encoding="utf-8").rstrip()
    path.write_text(content + f"\n- [ ] {text}\n", encoding="utf-8")
    return True


def _tasks_keyboard(list_name: str, tasks: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    """Build an inline keyboard with a ✅ button per task."""
    rows = []
    for line_idx, text in tasks:
        label = f"✅  {text[:40]}{'…' if len(text) > 40 else ''}"
        rows.append([InlineKeyboardButton(label, callback_data=f"tdone:{list_name}:{line_idx}")])
    return InlineKeyboardMarkup(rows)


async def _show_task_list(update: Update, list_name: str, vault: Path, from_callback: bool = False) -> None:
    """Display open tasks for a list with ✅ inline buttons."""
    tasks = _read_open_tasks(vault, list_name)
    if not tasks:
        text = f"*{list_name}* — no open tasks 🎉"
        if from_callback:
            await update.callback_query.edit_message_text(text, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, parse_mode="Markdown")
        return

    lines = [f"{i+1}. {t}" for i, (_, t) in enumerate(tasks)]
    text = f"*{list_name}*\n\n" + "\n".join(lines)
    kb = _tasks_keyboard(list_name, tasks)
    if from_callback:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)


async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /tasks           → inline menu of all task lists
    /tasks food-shop → open tasks in that list with ✅ buttons
    """
    vault_path = settings.obsidian_vault_path
    if not vault_path:
        await update.message.reply_text("OBSIDIAN_VAULT_PATH not configured.")
        return
    vault = Path(vault_path)

    if context.args:
        list_name = context.args[0].lower()
        if list_name not in TASK_LISTS:
            await update.message.reply_text(
                f"Unknown list '{list_name}'. Available: {', '.join(TASK_LISTS)}"
            )
            return
        await _show_task_list(update, list_name, vault)
        return

    # No args — show list menu
    rows = []
    for name in TASK_LISTS:
        tasks = _read_open_tasks(vault, name)
        count = f" ({len(tasks)})" if tasks else " ✓"
        rows.append([InlineKeyboardButton(f"{name}{count}", callback_data=f"tlist:{name}")])
    await update.message.reply_text(
        "Which task list?",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /add <list> <task description>
    /add inbox Call the dentist
    /add food-shop Oat milk
    """
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Usage: `/add <list> <task>`\n"
            f"Lists: {', '.join(TASK_LISTS)}",
            parse_mode="Markdown",
        )
        return

    list_name = context.args[0].lower()
    task_text = " ".join(context.args[1:])
    vault_path = settings.obsidian_vault_path
    if not vault_path:
        await update.message.reply_text("OBSIDIAN_VAULT_PATH not configured.")
        return

    new_list = list_name not in TASK_LISTS
    if new_list:
        TASK_LISTS[list_name] = f"tasks/{list_name}.md"

    _append_task(Path(vault_path), list_name, task_text)
    msg = f"{'Created *' + list_name + '* and added' if new_list else 'Added to *' + list_name + '*'}: {task_text}"
    await update.message.reply_text(msg, parse_mode="Markdown")


async def handle_task_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ✅ button taps and list navigation from /tasks menu."""
    query = update.callback_query
    await query.answer()
    data = query.data
    vault_path = settings.obsidian_vault_path
    if not vault_path:
        await query.edit_message_text("OBSIDIAN_VAULT_PATH not configured.")
        return
    vault = Path(vault_path)

    if data.startswith("tlist:"):
        list_name = data[6:]
        await _show_task_list(update, list_name, vault, from_callback=True)

    elif data.startswith("tdone:"):
        _, list_name, line_idx_str = data.split(":", 2)
        task_text = _complete_task(vault, list_name, int(line_idx_str))
        if task_text:
            # Refresh the list after marking complete
            tasks = _read_open_tasks(vault, list_name)
            if tasks:
                lines = [f"{i+1}. {t}" for i, (_, t) in enumerate(tasks)]
                text = f"✅ *{task_text}*\n\n*{list_name}*\n\n" + "\n".join(lines)
                kb = _tasks_keyboard(list_name, tasks)
            else:
                text = f"✅ *{task_text}*\n\n*{list_name}* — all done 🎉"
                kb = None
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        else:
            await query.edit_message_text("Couldn't find that task — it may have already been completed.")


# ---------------------------------------------------------------------------
# /cancel — ends any active conversation
# ---------------------------------------------------------------------------

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "Cancelled.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def build_app() -> Application:
    app = Application.builder().token(settings.telegram_bot_token).build()

    # ── ConversationHandlers (must be registered before generic handlers) ──
    cancel_handler = CommandHandler("cancel", cmd_cancel)

    event_conv = ConversationHandler(
        entry_points=[CommandHandler(["event", "cal", "calendar"], event_start)],
        states={
            EVENT_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_get_title)],
            EVENT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_get_date)],
            EVENT_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_get_time)],
            EVENT_LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_get_location)],
        },
        fallbacks=[cancel_handler],
        name="event_conv",
        persistent=False,
    )

    task_conv = ConversationHandler(
        entry_points=[CommandHandler(["task"], task_start)],
        states={
            TASK_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, task_get_desc)],
            TASK_LIST: [MessageHandler(filters.TEXT & ~filters.COMMAND, task_get_list)],
            TASK_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, task_get_date)],
            TASK_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, task_get_time)],
        },
        fallbacks=[cancel_handler],
        name="task_conv",
        persistent=False,
    )

    book_conv = ConversationHandler(
        entry_points=[CommandHandler("book", book_start)],
        states={
            BOOK_QUERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, book_get_query)],
        },
        fallbacks=[cancel_handler],
        name="book_conv",
        persistent=False,
    )

    daily_conv = ConversationHandler(
        entry_points=[CommandHandler("daily", daily_start)],
        states={
            DAILY_MOOD: [MessageHandler(filters.TEXT & ~filters.COMMAND, daily_get_mood)],
            DAILY_HABITS: [MessageHandler(filters.TEXT & ~filters.COMMAND, daily_get_habits)],
            DAILY_GRATEFUL: [MessageHandler(filters.TEXT & ~filters.COMMAND, daily_get_grateful)],
            DAILY_HIGHLIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, daily_get_highlight)],
        },
        fallbacks=[cancel_handler],
        name="daily_conv",
        persistent=False,
    )

    weekly_conv = ConversationHandler(
        entry_points=[CommandHandler("weekly", weekly_start)],
        states={
            WEEKLY_GOAL_1: [MessageHandler(filters.TEXT & ~filters.COMMAND, weekly_goal_1)],
            WEEKLY_GOAL_2: [MessageHandler(filters.TEXT & ~filters.COMMAND, weekly_goal_2)],
            WEEKLY_GOAL_3: [MessageHandler(filters.TEXT & ~filters.COMMAND, weekly_goal_3)],
        },
        fallbacks=[cancel_handler],
        name="weekly_conv",
        persistent=False,
    )

    app.add_handler(event_conv)
    app.add_handler(task_conv)
    app.add_handler(book_conv)
    app.add_handler(daily_conv)
    app.add_handler(weekly_conv)

    # ── Simple command handlers ─────────────────────────────────────────────
    app.add_handler(CommandHandler(["start", "help"], cmd_help))
    app.add_handler(CommandHandler(["create", "write", "alchemist"], cmd_create))
    app.add_handler(CommandHandler("note", cmd_note))
    app.add_handler(CommandHandler("todo", cmd_todo))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CallbackQueryHandler(handle_task_callback, pattern=r"^(tlist:|tdone:)"))
    app.add_handler(CommandHandler("todos", cmd_todos))
    app.add_handler(CommandHandler("shopping", cmd_shopping))
    app.add_handler(CommandHandler("email", cmd_email))
    app.add_handler(CommandHandler("research", cmd_research))
    app.add_handler(CommandHandler("draft", cmd_draft))
    app.add_handler(CommandHandler("context", cmd_context))
    app.add_handler(CommandHandler("digest", cmd_digest))
    app.add_handler(CommandHandler("monthly", cmd_monthly))
    app.add_handler(CommandHandler("getdaily", cmd_getdaily))
    app.add_handler(CommandHandler("getweekly", cmd_getweekly))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("status", cmd_status))

    # ── Free-form media handlers ────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VIDEO | filters.VIDEO_NOTE, handle_video))

    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        print(f"[TG ERROR] {context.error}", flush=True)
        import traceback
        traceback.print_exc()

    app.add_error_handler(error_handler)

    return app
