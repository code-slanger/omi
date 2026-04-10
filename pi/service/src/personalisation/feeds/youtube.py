"""
YouTube channel monitor.

Uses yt-dlp to fetch recent videos (metadata + optional audio download).
Transcribes audio with Whisper and summarizes with Gemini (long context)
or Haiku as fallback.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from dataclasses import dataclass

from ..llm.client import get_client
from ..transcription import transcribe_file
from . import state
from .config import YouTubeSource

logger = logging.getLogger(__name__)

_SUMMARIZE_SYSTEM = (
    "Summarize the following video transcript in 3-5 sentences. "
    "Identify the key topics covered, any important claims or insights, and the overall conclusion. "
    "Be direct — no preamble."
)

_DESCRIBE_SYSTEM = (
    "Based on the video title and description below, write a 2-sentence summary of what this video is about. "
    "Be direct — no preamble."
)


@dataclass
class VideoItem:
    title: str
    url: str
    channel_name: str
    summary: str
    transcript: str = ""


async def fetch_channel(source: YouTubeSource) -> list[VideoItem]:
    """Fetch new videos from a YouTube channel and optionally transcribe them."""
    loop = asyncio.get_event_loop()

    # Get video list via yt-dlp
    videos = await loop.run_in_executor(None, _get_channel_videos, source)
    if not videos:
        return []

    # Filter to unseen videos
    last_id = await state.get("youtube", source.channel_id, "last_video_id")
    new_videos = []
    new_last_id = None
    for v in videos:
        if v["id"] == last_id:
            break
        new_videos.append(v)
        if new_last_id is None:
            new_last_id = v["id"]

    new_videos = new_videos[: source.max_videos]
    if not new_videos:
        return []

    if new_last_id:
        await state.set_value("youtube", source.channel_id, "last_video_id", new_last_id)

    items: list[VideoItem] = []
    for video in new_videos:
        item = await _process_video(video, source)
        items.append(item)

    return items


def _get_channel_videos(source: YouTubeSource) -> list[dict]:
    """Run yt-dlp synchronously to get channel video list."""
    try:
        import yt_dlp

        url = f"https://www.youtube.com/channel/{source.channel_id}/videos"
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "playlist_items": f"1-{source.max_videos * 3}",  # fetch extra in case some are old
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info or "entries" not in info:
                return []
            return [
                {
                    "id": e.get("id", ""),
                    "title": e.get("title", ""),
                    "url": f"https://www.youtube.com/watch?v={e.get('id', '')}",
                    "description": e.get("description", ""),
                    "channel": e.get("channel", source.name),
                }
                for e in (info.get("entries") or [])
                if e.get("id")
            ]
    except Exception as e:
        logger.error(f"yt-dlp channel fetch failed for {source.channel_id}: {e}")
        return []


async def _process_video(video: dict, source: YouTubeSource) -> VideoItem:
    """Transcribe (if enabled) and summarize a video."""
    channel_name = source.name or video.get("channel", source.channel_id)
    transcript = ""
    summary = ""

    if source.transcribe and not source.summarize_only:
        transcript = await _download_and_transcribe(video["url"])

    llm = get_client("summarize")

    if transcript:
        try:
            summary = await llm.complete(_SUMMARIZE_SYSTEM, transcript[:50000], max_tokens=512)
        except Exception as e:
            logger.warning(f"Transcript summarize failed: {e}")
            summary = transcript[:500] + "..."
    else:
        # No transcript — summarize from title/description only
        meta = f"Title: {video['title']}\n\nDescription: {video.get('description', '')[:2000]}"
        try:
            summary = await llm.complete(_DESCRIBE_SYSTEM, meta, max_tokens=256)
        except Exception as e:
            logger.warning(f"Description summarize failed: {e}")
            summary = video.get("description", "")[:300]

    return VideoItem(
        title=video["title"],
        url=video["url"],
        channel_name=channel_name,
        summary=summary,
        transcript=transcript,
    )


async def _download_and_transcribe(video_url: str) -> str:
    """Download audio from YouTube video and transcribe with Whisper."""
    loop = asyncio.get_event_loop()

    with tempfile.TemporaryDirectory() as tmp_dir:
        audio_path = os.path.join(tmp_dir, "audio.wav")

        try:
            import yt_dlp
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "format": "bestaudio[ext=m4a]/bestaudio/best",
                "outtmpl": os.path.join(tmp_dir, "audio.%(ext)s"),
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "wav",
                    "preferredquality": "0",
                }],
                "postprocessor_args": ["-ar", "16000", "-ac", "1"],
            }
            await loop.run_in_executor(
                None,
                lambda: yt_dlp.YoutubeDL(ydl_opts).download([video_url])
            )
        except Exception as e:
            logger.error(f"Audio download failed for {video_url}: {e}")
            return ""

        if not os.path.exists(audio_path):
            # yt-dlp may name it differently
            wav_files = [f for f in os.listdir(tmp_dir) if f.endswith(".wav")]
            if not wav_files:
                return ""
            audio_path = os.path.join(tmp_dir, wav_files[0])

        try:
            return await transcribe_file(audio_path)
        except Exception as e:
            logger.error(f"Transcription failed for {video_url}: {e}")
            return ""
