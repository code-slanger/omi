"""
Daily digest builder.

Fetches all feed sources, builds a markdown document, saves it to the vault,
and returns the markdown text + date string.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from pathlib import Path

from ..config import settings
from ..feeds import config as feeds_config
from ..feeds.rss import fetch_rss, fetch_substack
from ..feeds.youtube import fetch_channel
from ..feeds.github import fetch_repo
from ..feeds.email_reader import fetch_emails, summarize_emails
from ..cal.client import get_events, tag_vault_note

logger = logging.getLogger(__name__)


async def build(target_date: date | None = None) -> tuple[str, str]:
    """
    Build the daily digest.

    Returns (markdown_text, date_str).
    Saves to vault/Daily/YYYY-MM-DD.md if vault is configured.
    """
    cfg = feeds_config.load()
    today = target_date or date.today()
    date_str = today.strftime("%Y-%m-%d")

    sections: list[str] = [f"# Daily Digest — {date_str}\n"]

    # ── Calendar ──────────────────────────────────────────────────────────────
    try:
        events = await get_events(days_ahead=1)
        today_events = [e for e in events if e.start.date() == today]
        if today_events:
            lines = [f"- {e.start.strftime('%H:%M')}–{e.end.strftime('%H:%M')} **{e.title}**"
                     + (f" _{e.location}_" if e.location else "")
                     for e in today_events]
            sections.append("## Calendar\n\n" + "\n".join(lines))
    except Exception as e:
        logger.warning(f"Calendar section failed: {e}")

    # ── RSS / News ─────────────────────────────────────────────────────────────
    rss_items = []
    for source in cfg.rss:
        try:
            items = await fetch_rss(source)
            rss_items.extend(items)
        except Exception as e:
            logger.warning(f"RSS {source.url} failed: {e}")

    if rss_items:
        lines = []
        for item in rss_items:
            lines.append(f"### [{item.title}]({item.url})\n_{item.source_name}_\n\n{item.summary}")
        sections.append("## News & Articles\n\n" + "\n\n---\n\n".join(lines))

    # ── YouTube ────────────────────────────────────────────────────────────────
    yt_items = []
    for source in cfg.youtube:
        try:
            items = await fetch_channel(source)
            yt_items.extend(items)
        except Exception as e:
            logger.warning(f"YouTube {source.channel_id} failed: {e}")

    if yt_items:
        lines = []
        for item in yt_items:
            lines.append(f"### [{item.title}]({item.url})\n_{item.channel_name}_\n\n{item.summary}")
        sections.append("## YouTube\n\n" + "\n\n---\n\n".join(lines))

    # ── Substack ───────────────────────────────────────────────────────────────
    sub_items = []
    for source in cfg.substack:
        try:
            items = await fetch_substack(source)
            sub_items.extend(items)
        except Exception as e:
            logger.warning(f"Substack {source.url} failed: {e}")

    if sub_items:
        lines = []
        for item in sub_items:
            lines.append(f"### [{item.title}]({item.url})\n_{item.source_name}_\n\n{item.summary}")
        sections.append("## Substack\n\n" + "\n\n---\n\n".join(lines))

    # ── GitHub ─────────────────────────────────────────────────────────────────
    gh_items = []
    for source in cfg.github:
        try:
            updates = await fetch_repo(source)
            gh_items.extend(updates)
        except Exception as e:
            logger.warning(f"GitHub {source.repo} failed: {e}")

    if gh_items:
        lines = []
        for u in gh_items:
            badge = {"commit": "🔨", "pull": "🔀", "release": "🚀"}.get(u.kind, "•")
            body = f"\n\n> {u.body[:200]}" if u.body else ""
            lines.append(f"- {badge} [{u.title}]({u.url})" + (f" — _{u.author}_" if u.author else "") + body)
        sections.append("## GitHub\n\n" + "\n".join(lines))

    # ── Email ──────────────────────────────────────────────────────────────────
    try:
        email_items = await fetch_emails(cfg.email)
        if email_items and cfg.email.summarize:
            email_summary = await summarize_emails(email_items)
            if email_summary:
                sections.append(f"## Email ({len(email_items)} unread)\n\n{email_summary}")
        elif email_items:
            lines = [f"- **{e.subject}** — {e.sender}" for e in email_items]
            sections.append(f"## Email ({len(email_items)} unread)\n\n" + "\n".join(lines))
    except Exception as e:
        logger.warning(f"Email section failed: {e}")

    # ── Footer ─────────────────────────────────────────────────────────────────
    sections.append(f"---\n_Generated {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}_")

    if not any(s for s in sections[1:] if not s.startswith("---")):
        sections.append("_No new items today._")

    markdown = "\n\n".join(sections)

    # Save to vault
    await _save_to_vault(markdown, date_str)
    # Tag calendar events onto the note
    try:
        await tag_vault_note(today)
    except Exception as e:
        logger.debug(f"Calendar tag failed: {e}")

    return markdown, date_str


async def _save_to_vault(markdown: str, date_str: str) -> None:
    if not settings.obsidian_vault_path:
        return
    vault = Path(settings.obsidian_vault_path)
    daily_dir = vault / "Daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    note_path = daily_dir / f"{date_str}.md"
    note_path.write_text(markdown)
    logger.info(f"Digest saved to vault: {note_path}")
