"""
Shared Whisper singleton — import this everywhere instead of defining _get_whisper() locally.
Uses faster-whisper with CPU int8 quantisation (suitable for Pi 5).
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from faster_whisper import WhisperModel

from .config import settings

_whisper: WhisperModel | None = None


def get_model() -> WhisperModel:
    global _whisper
    if _whisper is None:
        _whisper = WhisperModel(settings.whisper_model, device="cpu", compute_type="int8")
    return _whisper


async def transcribe_file(file_path: str | Path) -> str:
    """Transcribe an audio/video file. Returns joined segment text."""
    loop = asyncio.get_event_loop()
    model = get_model()
    segments, _ = await loop.run_in_executor(
        None, lambda: model.transcribe(str(file_path))
    )
    return " ".join(s.text.strip() for s in segments).strip()


async def transcribe_bytes(data: bytes, suffix: str = ".wav") -> str:
    """Write bytes to a temp file, transcribe, and return text."""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        return await transcribe_file(tmp_path)
    finally:
        os.unlink(tmp_path)
