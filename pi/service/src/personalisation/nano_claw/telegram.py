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
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..config import settings
from ..transcription import transcribe_file
from . import router

logger = logging.getLogger(__name__)


async def _transcribe(file_path: str) -> str:
    return await transcribe_file(file_path)


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
# Slash command handlers
# ---------------------------------------------------------------------------

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "*Nano Claw*\n\n"
        "Messages are automatically classified — just talk naturally.\n\n"

        "*Creative (Alchemist OS — writes in your voice):*\n"
        "`/create <prompt>` or `/write <prompt>`\n\n"

        "*Cognitive (Nano Claw — practical tasks):*\n"
        "`/note <content>` — save note to Obsidian\n"
        "`/todo <task>` — create task note\n"
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

        "`/status` — system info (shows your chat ID for digest)"
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


async def cmd_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Send an email. Format: /email to@example.com | Subject | Body
    Or describe it naturally: /email John about the meeting tomorrow at 3pm
    """
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
    # Pass pipe-separated format or natural description — agent handles both
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
    from ..embeddings.retrieval import retrieve
    results = retrieve(settings.nano_claw_user_id, query, n_results=5)
    if not results:
        await update.message.reply_text("No relevant context found.")
        return
    parts = [f"*{i+1}.* {r['text'][:400]}" for i, r in enumerate(results)]
    await _reply(update, "\n\n".join(parts))


async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Build and send today's digest link (or a specific date: /digest 2026-04-10)."""
    from datetime import date
    date_str = (context.args[0] if context.args else None) or date.today().isoformat()
    base = settings.digest_base_url.rstrip("/") or "http://192.168.0.27:8000"
    url = f"{base}/digest/{date_str}"
    await update.message.reply_text(
        f"[Daily Digest — {date_str}]({url})\n\nOr build on demand: `POST /digest/build`",
        parse_mode="Markdown",
        disable_web_page_preview=False,
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from ..embeddings.index import collection_count
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
# App factory
# ---------------------------------------------------------------------------

def build_app() -> Application:
    app = Application.builder().token(settings.telegram_bot_token).build()

    app.add_handler(CommandHandler(["start", "help"], cmd_help))
    app.add_handler(CommandHandler(["create", "write", "alchemist"], cmd_create))
    app.add_handler(CommandHandler("note", cmd_note))
    app.add_handler(CommandHandler("todo", cmd_todo))
    app.add_handler(CommandHandler("email", cmd_email))
    app.add_handler(CommandHandler("research", cmd_research))
    app.add_handler(CommandHandler("draft", cmd_draft))
    app.add_handler(CommandHandler("context", cmd_context))
    app.add_handler(CommandHandler("digest", cmd_digest))
    app.add_handler(CommandHandler("status", cmd_status))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VIDEO | filters.VIDEO_NOTE, handle_video))

    return app
