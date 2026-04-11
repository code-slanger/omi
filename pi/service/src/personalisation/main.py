import asyncio
import io
import logging
import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# Suppress noisy third-party loggers
logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
logging.getLogger("torch").setLevel(logging.ERROR)

# Filter health check spam from uvicorn access logs
class _HealthFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "GET /health" not in record.getMessage()

logging.getLogger("uvicorn.access").addFilter(_HealthFilter())

from .config import settings
from .embeddings.index import add_documents, collection_count, delete_collection
from .preprocessors import audio as audio_prep
from .preprocessors import image as image_prep
from .preprocessors import text as text_prep
from .preprocessors import video as video_prep
from .profile.builder import build_profile
from .agents.writer import generate
from .storage import append_feedback, list_uploads, load_profile, save_generation, save_upload
from .transcription import get_model as get_whisper_model

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan — background tasks
# ---------------------------------------------------------------------------

async def _photo_pruner() -> None:
    """Delete photos older than 3 days every 6 hours."""
    while True:
        try:
            cutoff = datetime.now(timezone.utc).timestamp() - 3 * 86400
            photo_dir = Path(settings.data_dir) / "uploads"
            removed = 0
            for jpg in photo_dir.rglob("*.jpg"):
                if jpg.stat().st_mtime < cutoff:
                    jpg.unlink()
                    removed += 1
            if removed:
                logger.warning("Photo pruner: removed %d files older than 3 days", removed)
        except Exception as e:
            logger.warning("Photo pruner error: %s", e)
        await asyncio.sleep(6 * 3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks: list[asyncio.Task] = []
    tg_app = None

    # Vault watcher — re-indexes Obsidian vault changes into ChromaDB
    if settings.obsidian_vault_path:
        from .nano_claw import vault_watcher
        tasks.append(asyncio.create_task(
            vault_watcher.watch(settings.nano_claw_user_id),
            name="vault-watcher",
        ))
        logger.info("Vault watcher started")

    # Telegram bot — polling mode
    if settings.telegram_bot_token:
        from .nano_claw.telegram import build_app as build_tg_app
        tg_app = build_tg_app()
        await tg_app.initialize()
        await tg_app.start()
        await tg_app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram bot started")

    # Daily digest scheduler
    from .digest import scheduler as digest_scheduler
    from .feeds.config import write_example as write_feeds_example
    write_feeds_example()  # create feeds.yaml if absent
    tasks.append(asyncio.create_task(
        digest_scheduler.run(),
        name="digest-scheduler",
    ))
    logger.info("Digest scheduler started")

    # Photo pruner — delete images older than 3 days, runs every 6 hours
    tasks.append(asyncio.create_task(_photo_pruner(), name="photo-pruner"))

    yield

    # Shutdown
    if tg_app:
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()

    for task in tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Personalisation Service", version="0.2.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Media type detection
# ---------------------------------------------------------------------------

_MIME_TO_TYPE = {
    "text/plain": "text",
    "text/markdown": "text",
    "application/pdf": "text",
    "audio/mpeg": "audio",
    "audio/mp3": "audio",
    "audio/wav": "audio",
    "audio/x-wav": "audio",
    "audio/aac": "audio",
    "audio/mp4": "audio",
    "audio/ogg": "audio",
    "audio/flac": "audio",
    "image/jpeg": "image",
    "image/png": "image",
    "image/heic": "image",
    "image/webp": "image",
    "image/gif": "image",
    "video/mp4": "video",
    "video/quicktime": "video",
    "video/x-msvideo": "video",
    "video/webm": "video",
}

_EXT_TO_TYPE = {
    ".txt": "text", ".md": "text", ".pdf": "text",
    ".mp3": "audio", ".wav": "audio", ".m4a": "audio",
    ".aac": "audio", ".ogg": "audio", ".flac": "audio",
    ".jpg": "image", ".jpeg": "image", ".png": "image",
    ".heic": "image", ".webp": "image", ".gif": "image",
    ".mp4": "video", ".mov": "video", ".avi": "video", ".webm": "video",
}


def _detect_media_type(content_type: str, filename: str) -> str | None:
    if mt := _MIME_TO_TYPE.get(content_type):
        return mt
    ext = Path(filename).suffix.lower()
    return _EXT_TO_TYPE.get(ext)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    prompt: str


class FeedbackRequest(BaseModel):
    generation_id: str
    prompt: str
    output: str
    action: Literal["accept", "edit", "reject"]
    edited_output: str | None = None


class OmiSegment(BaseModel):
    text: str
    speaker: str | None = None
    speaker_id: int | None = None
    is_user: bool | None = None
    start: float | None = None
    end: float | None = None


class OmiWebhookRequest(BaseModel):
    session_id: str
    segments: list[OmiSegment] = []
    transcript: str | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _check_webhook_secret(key: str | None) -> None:
    """Raise 403 if webhook_secret is configured and key doesn't match."""
    if settings.webhook_secret and key != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")


@app.get("/health")
def health():
    return {"status": "ok", "writer_model": settings.generation_model, "agent_model": settings.nano_claw_model}


@app.post("/users/{user_id}/upload")
async def upload_file(
    user_id: str,
    file: UploadFile = File(...),
    source_type: str = Query(default="reference", description="'own_writing' or 'reference'"),
):
    """Upload a media file, preprocess, and add to the user's embedding corpus."""
    filename = file.filename or f"upload_{uuid.uuid4().hex}"
    content_type = (file.content_type or "").split(";")[0].strip()

    media_type = _detect_media_type(content_type, filename)
    if not media_type:
        raise HTTPException(
            400,
            "Unsupported file type. Accepted: text (.txt, .md, .pdf), "
            "audio (.mp3, .wav, .m4a), image (.jpg, .png, .heic), video (.mp4, .mov)",
        )

    content = await file.read()
    await save_upload(user_id, media_type, filename, content)
    documents = _preprocess(content, filename, media_type, source_type)
    add_documents(user_id, documents)

    return {
        "status": "indexed",
        "filename": filename,
        "media_type": media_type,
        "chunks_indexed": len(documents),
        "corpus_size": collection_count(user_id),
    }


@app.get("/users/{user_id}/uploads")
def get_uploads(user_id: str, media_type: str | None = None):
    return {"uploads": list_uploads(user_id, media_type)}


@app.delete("/users/{user_id}/corpus")
def clear_corpus(user_id: str):
    delete_collection(user_id)
    return {"status": "cleared"}


@app.get("/users/{user_id}/profile")
async def get_profile(user_id: str):
    profile = await load_profile(user_id)
    if not profile:
        raise HTTPException(404, "No profile found. Upload content first, then POST /users/{user_id}/profile/rebuild.")
    return profile


@app.post("/users/{user_id}/profile/rebuild")
async def rebuild_profile(user_id: str):
    if collection_count(user_id) == 0:
        raise HTTPException(400, "No corpus found. Upload content first.")
    profile = await build_profile(user_id)
    return profile.model_dump()


@app.post("/users/{user_id}/generate")
async def generate_text(user_id: str, req: GenerateRequest):
    """Generate personalised creative content in the user's voice."""
    text = await generate(user_id, req.prompt)
    return {"generation_id": str(uuid.uuid4()), "text": text}


@app.post("/users/{user_id}/process-audio")
async def process_audio(user_id: str, file: UploadFile = File(...)):
    """Transcribe audio locally with Whisper, then generate in the user's voice."""
    content = await file.read()
    suffix = Path(file.filename or "audio.m4a").suffix or ".m4a"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        segments, _ = get_whisper_model().transcribe(tmp_path)
        transcript = " ".join(s.text.strip() for s in segments)
    finally:
        os.unlink(tmp_path)

    from .nano_claw.router import route
    text, mode = await route(user_id, transcript)
    return {"generation_id": str(uuid.uuid4()), "transcript": transcript, "text": text, "mode": mode}


@app.post("/webhooks/omi/{user_id}")
async def omi_webhook(user_id: str, req: OmiWebhookRequest, key: str | None = Query(default=None)):
    """
    Omi glasses webhook. Receives transcript, classifies intent, and routes to the
    appropriate agent:
      - creative content → writer agent (prose in user's voice)
      - tasks / notes / emails → cognitive agent (Haiku + Obsidian tools)

    Payloads shorter than OMI_MIN_WORDS are silently ignored (filters noise).
    """
    _check_webhook_secret(key)
    transcript = req.transcript or " ".join(
        s.text.strip() for s in req.segments if s.text.strip()
    )
    transcript = transcript.strip()

    if len(transcript.split()) < settings.omi_min_words:
        return {"status": "ignored", "reason": "transcript too short", "session_id": req.session_id}

    from .nano_claw.router import route
    text, mode = await route(user_id, transcript)
    generation_id = str(uuid.uuid4())

    await save_generation(
        user_id=user_id,
        source=f"omi-{mode}",
        transcript=transcript,
        text=text,
        generation_id=generation_id,
    )

    return {
        "status": "generated",
        "mode": mode,
        "generation_id": generation_id,
        "session_id": req.session_id,
        "transcript": transcript,
        "text": text,
    }


@app.post("/webhooks/omi/{user_id}/audio")
async def omi_audio_webhook(
    user_id: str,
    request: Request,
    sample_rate: int = Query(default=16000),
    key: str | None = Query(default=None),
):
    """
    Omi raw audio bytes webhook.
    Receives PCM16 mono audio directly from the Omi app (no cloud transcription).
    Transcribes locally with Whisper, then routes to creative or cognitive agent.

    Omi app setting:
      Integration type: Audio Bytes
      Endpoint: http://192.168.0.27:8000/webhooks/omi/{user_id}/audio
    """
    _check_webhook_secret(key)
    import wave

    try:
        pcm_bytes = await request.body()
    except Exception:
        return {"status": "ignored", "reason": "client disconnected"}
    if not pcm_bytes:
        return {"status": "ignored", "reason": "empty body"}

    # Sanity check: PCM16 = 2 bytes per sample
    num_samples = len(pcm_bytes) // 2
    duration_secs = num_samples / sample_rate
    if duration_secs < 1.0:
        return {"status": "ignored", "reason": "audio too short", "duration_secs": round(duration_secs, 2)}

    # Wrap raw PCM16 in a WAV container so Whisper can read it
    wav_buf = io.BytesIO()
    with wave.open(wav_buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)           # int16 = 2 bytes
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    wav_buf.seek(0)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(wav_buf.read())
        tmp_path = tmp.name

    try:
        segments, _ = get_whisper_model().transcribe(
            tmp_path,
            language="en",
            vad_filter=True,
            condition_on_previous_text=False,
        )
        transcript = " ".join(s.text.strip() for s in segments).strip()
    finally:
        os.unlink(tmp_path)

    logger.info("Transcript: %r (%d words)", transcript, len(transcript.split()))
    if not transcript or len(transcript.split()) < settings.omi_min_words:
        logger.debug("Ignored — too short (min=%d)", settings.omi_min_words)
        return {"status": "ignored", "reason": "transcript too short", "transcript": transcript}

    if settings.omi_wake_word and settings.omi_wake_word.lower() not in transcript.lower():
        return {"status": "ignored", "reason": "wake word not detected", "transcript": transcript}

    # Strip wake word before routing so the agent doesn't see it
    clean = transcript
    if settings.omi_wake_word:
        import re
        clean = re.sub(re.escape(settings.omi_wake_word), "", transcript, flags=re.IGNORECASE).strip(" ,.")

    # Check for direct vault commands before sending to AI
    from .nano_claw.commands import match_voice_command, run as run_command
    cmd = match_voice_command(clean)
    if cmd:
        text = run_command(cmd)
        if settings.telegram_bot_token and settings.telegram_chat_id:
            try:
                from telegram import Bot
                bot = Bot(token=settings.telegram_bot_token)
                await bot.send_message(
                    chat_id=settings.telegram_chat_id,
                    text=text,
                )
            except Exception as e:
                logger.warning("Telegram send failed: %s", e)
        return {
            "status": "command",
            "command": cmd,
            "transcript": transcript,
            "text": text,
        }

    from .nano_claw.router import route
    text, mode = await route(user_id, clean)
    generation_id = str(uuid.uuid4())

    await save_generation(
        user_id=user_id,
        source=f"omi-audio-{mode}",
        transcript=clean,
        text=text,
        generation_id=generation_id,
    )

    if settings.telegram_bot_token and settings.telegram_chat_id:
        try:
            from telegram import Bot
            bot = Bot(token=settings.telegram_bot_token)
            await bot.send_message(
                chat_id=settings.telegram_chat_id,
                text=f"🎙 _{clean}_\n\n{text}",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning("Telegram send failed: %s", e)

    return {
        "status": "generated",
        "mode": mode,
        "generation_id": generation_id,
        "transcript": transcript,
        "text": text,
        "duration_secs": round(duration_secs, 2),
    }


@app.post("/webhooks/omi/{user_id}/photo")
async def omi_photo_webhook(user_id: str, request: Request, key: str | None = Query(default=None)):
    """
    Receives a raw JPEG directly from the glasses over WiFi.
    Firmware posts here after every photo capture — no Omi app or BLE bridge required.

    Firmware setting: PI_SERVICE_URL / PI_USER_ID in config.h
    """
    _check_webhook_secret(key)
    try:
        jpeg_bytes = await request.body()
    except Exception:
        return {"status": "ignored", "reason": "client disconnected"}
    if len(jpeg_bytes) < 100:
        return {"status": "ignored", "reason": "too small"}

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"photo_{ts}.jpg"
    await save_upload(user_id, "image", filename, jpeg_bytes)
    logger.info("Photo saved: %s (%d bytes)", filename, len(jpeg_bytes))

    return {"status": "saved", "filename": filename}


@app.get("/digest/{date_str}", response_class=HTMLResponse)
async def get_digest(date_str: str):
    """
    Serve the daily digest as an HTML page.
    date_str: YYYY-MM-DD  (e.g. 2026-04-10)
    Falls back to building today's digest on demand if the file doesn't exist.
    """
    from pathlib import Path
    from datetime import date
    import re

    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        raise HTTPException(400, "date_str must be YYYY-MM-DD")

    # Try vault first
    markdown = ""
    if settings.obsidian_vault_path:
        note_path = Path(settings.obsidian_vault_path) / "Daily" / f"{date_str}.md"
        if note_path.exists():
            markdown = note_path.read_text()

    if not markdown:
        # Build on demand
        from .digest.builder import build
        target = date.fromisoformat(date_str)
        markdown, _ = await build(target)

    # Render markdown → HTML
    try:
        from markdown_it import MarkdownIt
        md = MarkdownIt()
        body = md.render(markdown)
    except ImportError:
        # Fallback: plain text wrapped in pre
        body = f"<pre>{markdown}</pre>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Daily Digest — {date_str}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 800px; margin: 2rem auto; padding: 0 1rem; line-height: 1.6; }}
    h1, h2, h3 {{ border-bottom: 1px solid #eee; padding-bottom: 0.3em; }}
    a {{ color: #0366d6; }}
    blockquote {{ border-left: 4px solid #ddd; padding-left: 1em; color: #555; }}
    hr {{ border: none; border-top: 1px solid #eee; margin: 2rem 0; }}
  </style>
</head>
<body>
{body}
</body>
</html>"""
    return HTMLResponse(content=html)


@app.post("/digest/build", status_code=202)
async def trigger_digest_build():
    """Manually trigger a digest build for today."""
    from .digest.builder import build
    markdown, date_str = await build()
    return {"status": "built", "date": date_str, "url": f"/digest/{date_str}"}


@app.post("/users/{user_id}/feedback")
async def submit_feedback(user_id: str, req: FeedbackRequest):
    record = {
        **req.model_dump(),
        "user_id": user_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await append_feedback(user_id, record)
    return {"status": "recorded"}


# ---------------------------------------------------------------------------
# Preprocessing dispatch
# ---------------------------------------------------------------------------

def _preprocess(content: bytes, filename: str, media_type: str, source_type: str = "reference") -> list[dict]:
    if media_type == "text":
        return text_prep.preprocess(content, filename, source_type)
    if media_type == "audio":
        return [audio_prep.preprocess(content, filename)]
    if media_type == "image":
        return [image_prep.preprocess(content, filename)]
    if media_type == "video":
        return [video_prep.preprocess(content, filename)]
    return []
