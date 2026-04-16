"""
Transcription via OpenAI Whisper API (whisper-1).
Requires WHISPER_TOKEN in .env.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import httpx

from .config import settings

_WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"


async def transcribe_file(file_path: str | Path) -> str:
    """Transcribe an audio/video file via the OpenAI Whisper API."""
    if not settings.whisper_token:
        raise RuntimeError("WHISPER_TOKEN is not set in .env")

    file_path = Path(file_path)
    async with httpx.AsyncClient(timeout=120) as client:
        with file_path.open("rb") as f:
            response = await client.post(
                _WHISPER_URL,
                headers={"Authorization": f"Bearer {settings.whisper_token}"},
                data={"model": "whisper-1"},
                files={"file": (file_path.name, f, "audio/mpeg")},
            )
    response.raise_for_status()
    return response.json().get("text", "").strip()


async def transcribe_bytes(data: bytes, suffix: str = ".wav") -> str:
    """Write bytes to a temp file, transcribe, and return text."""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        return await transcribe_file(tmp_path)
    finally:
        os.unlink(tmp_path)
