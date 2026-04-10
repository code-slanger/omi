"""
RSS and Substack feed fetcher.

Returns new items (not previously seen) for the daily digest.
Uses feedparser for parsing; summarizes with the configured LLM.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ..llm.client import get_client
from . import state
from .config import RSSSource, SubstackSource

logger = logging.getLogger(__name__)

_SUMMARIZE_SYSTEM = (
    "Summarize the following article in 2-3 clear sentences. "
    "Be direct — no preamble, no 'This article discusses...' framing. "
    "Just the key points."
)


@dataclass
class FeedItem:
    title: str
    url: str
    summary: str  # raw or LLM-summarized
    source_name: str


async def fetch_rss(source: RSSSource) -> list[FeedItem]:
    return await _fetch_feed(
        url=source.url,
        name=source.name or source.url,
        summarize=source.summarize,
        max_items=source.max_items,
        section="rss",
    )


async def fetch_substack(source: SubstackSource) -> list[FeedItem]:
    return await _fetch_feed(
        url=source.url,
        name=source.name or source.url,
        summarize=source.summarize,
        max_items=source.max_items,
        section="substack",
    )


async def _fetch_feed(
    url: str,
    name: str,
    summarize: bool,
    max_items: int,
    section: str,
) -> list[FeedItem]:
    import asyncio
    import feedparser

    loop = asyncio.get_event_loop()
    feed = await loop.run_in_executor(None, feedparser.parse, url)

    if feed.bozo and not feed.entries:
        logger.warning(f"Failed to parse feed {url}: {feed.bozo_exception}")
        return []

    seen = await state.get_seen_guids(section, url)
    new_entries = [e for e in feed.entries if e.get("id", e.get("link", "")) not in seen]
    new_entries = new_entries[:max_items]

    if not new_entries:
        return []

    items: list[FeedItem] = []
    client = get_client("summarize") if summarize else None

    for entry in new_entries:
        content = _extract_content(entry)
        if summarize and client and content:
            try:
                summary = await client.complete(_SUMMARIZE_SYSTEM, content[:8000], max_tokens=256)
            except Exception as e:
                logger.warning(f"Summarize failed for {entry.get('link')}: {e}")
                summary = entry.get("summary", "")[:400]
        else:
            summary = entry.get("summary", "")[:400]

        items.append(FeedItem(
            title=entry.get("title", "Untitled"),
            url=entry.get("link", ""),
            summary=summary,
            source_name=name,
        ))

    # Mark new items as seen
    new_guids = [e.get("id", e.get("link", "")) for e in new_entries]
    await state.mark_guids_seen(section, url, new_guids)

    return items


def _extract_content(entry: dict) -> str:
    """Extract readable text from a feed entry."""
    # Prefer full content over summary
    if content := entry.get("content"):
        if isinstance(content, list) and content:
            return content[0].get("value", "")
    if summary := entry.get("summary"):
        return summary
    return entry.get("title", "")
