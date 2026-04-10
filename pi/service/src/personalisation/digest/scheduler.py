"""
Daily digest scheduler.

Runs as a background asyncio task. At the configured time (UTC) each day:
  1. Builds the digest (fetches all feeds)
  2. Saves to vault/Daily/YYYY-MM-DD.md
  3. Posts a Telegram message with a summary and a link to the full digest

The local digest URL is: http://<PI_IP>:8000/digest/<date>
Set DIGEST_BASE_URL in .env to override (e.g. if behind a reverse proxy).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from ..config import settings
from . import builder

logger = logging.getLogger(__name__)


async def run() -> None:
    """Long-running background task — runs forever, firing once per day."""
    logger.info("Digest scheduler started")

    while True:
        wait_secs = _seconds_until_next_run()
        logger.info(f"Next digest in {wait_secs / 3600:.1f}h")
        await asyncio.sleep(wait_secs)

        try:
            await _run_once()
        except Exception as e:
            logger.error(f"Digest run failed: {e}", exc_info=True)


async def _run_once() -> None:
    logger.info("Building daily digest...")
    markdown, date_str = await builder.build()

    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.info(f"Digest built ({date_str}). No Telegram chat configured — skipping post.")
        return

    # Build message
    base_url = settings.digest_base_url.rstrip("/") or f"http://192.168.0.27:8000"
    digest_url = f"{base_url}/digest/{date_str}"

    # Extract a short preview — take first non-header paragraph
    preview_lines = []
    for line in markdown.split("\n"):
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith("---") and not stripped.startswith("_"):
            preview_lines.append(stripped)
        if len(preview_lines) >= 3:
            break
    preview = " ".join(preview_lines)[:400]

    message = (
        f"📋 *Daily Digest — {date_str}*\n\n"
        f"{preview}\n\n"
        f"[Read full digest]({digest_url})"
    )

    await _send_telegram(message)
    logger.info(f"Digest posted to Telegram for {date_str}")


async def _send_telegram(text: str) -> None:
    from telegram import Bot
    bot = Bot(token=settings.telegram_bot_token)
    # Split if needed
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        try:
            await bot.send_message(
                chat_id=settings.telegram_chat_id,
                text=chunk,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")


def _seconds_until_next_run() -> float:
    """Calculate seconds until the next configured digest_time (UTC)."""
    from ..feeds.config import load as load_feeds

    try:
        cfg = load_feeds()
        time_str = cfg.digest_time  # "HH:MM"
        hh, mm = (int(x) for x in time_str.split(":"))
    except Exception:
        hh, mm = 8, 0  # default 08:00 UTC

    now = datetime.now(timezone.utc)
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)

    return (target - now).total_seconds()
