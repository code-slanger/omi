"""
Calendar integration via CalDAV.

Provides:
  - get_events(days_ahead)  — fetch upcoming events
  - add_event(...)          — create a new calendar event
  - tag_vault_note(date)    — append today's events to the daily vault note

Set CALDAV_URL, CALDAV_USER, CALDAV_PASSWORD in .env.
Also supports ICAL_URL for read-only iCal (Google Calendar public URL, etc.)
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from ..config import settings

logger = logging.getLogger(__name__)


@dataclass
class CalEvent:
    title: str
    start: datetime
    end: datetime
    location: str = ""
    description: str = ""
    uid: str = ""


async def get_events(days_ahead: int = 7) -> list[CalEvent]:
    """Fetch upcoming calendar events. Tries CalDAV first, falls back to iCal."""
    loop = asyncio.get_event_loop()

    if settings.caldav_url:
        return await loop.run_in_executor(None, _fetch_caldav, days_ahead)
    if settings.ical_url:
        return await loop.run_in_executor(None, _fetch_ical, days_ahead)

    return []


async def add_event(
    title: str,
    start: datetime,
    end: datetime,
    location: str = "",
    description: str = "",
) -> str:
    """Create a new event via CalDAV. Returns confirmation message."""
    if not settings.caldav_url:
        return "CalDAV not configured. Set CALDAV_URL, CALDAV_USER, CALDAV_PASSWORD."

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _create_caldav_event, title, start, end, location, description
    )


async def tag_vault_note(target_date: date | None = None) -> str:
    """
    Append today's events as a YAML front-matter tag and section in the daily
    vault note (vault/Daily/YYYY-MM-DD.md).  Creates the note if absent.
    Returns the note path.
    """
    from pathlib import Path
    from ..config import settings

    if not settings.obsidian_vault_path:
        return ""

    target = target_date or date.today()
    date_str = target.strftime("%Y-%m-%d")
    vault = Path(settings.obsidian_vault_path)
    daily_dir = vault / "Daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    note_path = daily_dir / f"{date_str}.md"

    events = await get_events(days_ahead=1)
    today_events = [
        e for e in events
        if e.start.date() == target
    ]

    event_lines = "\n".join(
        f"- {e.start.strftime('%H:%M')}–{e.end.strftime('%H:%M')} {e.title}"
        + (f" @ {e.location}" if e.location else "")
        for e in today_events
    ) or "_No events_"

    section = f"\n\n## Calendar — {date_str}\n\n{event_lines}\n"

    if note_path.exists():
        existing = note_path.read_text()
        if "## Calendar" not in existing:
            note_path.write_text(existing + section)
    else:
        frontmatter = f"---\ndate: {date_str}\ncreated: {datetime.now().isoformat()}\n---\n"
        note_path.write_text(frontmatter + f"\n# {date_str}\n" + section)

    return str(note_path)


def _fetch_caldav(days_ahead: int) -> list[CalEvent]:
    try:
        import caldav
        from icalendar import Calendar as ICal

        client = caldav.DAVClient(
            url=settings.caldav_url,
            username=settings.caldav_user,
            password=settings.caldav_password,
        )
        principal = client.principal()
        calendars = principal.calendars()

        now = datetime.now(timezone.utc)
        end = now + timedelta(days=days_ahead)

        events: list[CalEvent] = []
        for cal in calendars:
            for event in cal.date_search(start=now, end=end, expand=True):
                raw = ICal.from_ical(event.data)
                for component in raw.walk():
                    if component.name == "VEVENT":
                        ev = _parse_ical_component(component)
                        if ev:
                            events.append(ev)

        return sorted(events, key=lambda e: e.start)
    except Exception as exc:
        logger.error(f"CalDAV fetch failed: {exc}")
        return []


def _fetch_ical(days_ahead: int) -> list[CalEvent]:
    """Fetch events from a read-only iCal URL (e.g. Google Calendar public link)."""
    try:
        import httpx
        from icalendar import Calendar as ICal

        resp = httpx.get(settings.ical_url, timeout=15)
        resp.raise_for_status()
        raw = ICal.from_ical(resp.content)

        now = datetime.now(timezone.utc)
        end = now + timedelta(days=days_ahead)
        events: list[CalEvent] = []

        for component in raw.walk():
            if component.name == "VEVENT":
                ev = _parse_ical_component(component)
                if ev and now <= ev.start <= end:
                    events.append(ev)

        return sorted(events, key=lambda e: e.start)
    except Exception as exc:
        logger.error(f"iCal fetch failed: {exc}")
        return []


def _parse_ical_component(component) -> CalEvent | None:
    try:
        dtstart = component.get("dtstart")
        dtend = component.get("dtend")
        if not dtstart or not dtend:
            return None

        start = dtstart.dt
        end = dtend.dt

        # Normalize to datetime with UTC if date-only
        if isinstance(start, date) and not isinstance(start, datetime):
            start = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
        if isinstance(end, date) and not isinstance(end, datetime):
            end = datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc)

        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)

        return CalEvent(
            title=str(component.get("summary", "Untitled")),
            start=start,
            end=end,
            location=str(component.get("location", "")),
            description=str(component.get("description", ""))[:500],
            uid=str(component.get("uid", "")),
        )
    except Exception:
        return None


def _create_caldav_event(
    title: str,
    start: datetime,
    end: datetime,
    location: str,
    description: str,
) -> str:
    try:
        import caldav
        from icalendar import Calendar as ICal, Event

        client = caldav.DAVClient(
            url=settings.caldav_url,
            username=settings.caldav_user,
            password=settings.caldav_password,
        )
        cal = client.principal().calendars()[0]

        ical = ICal()
        event = Event()
        event.add("summary", title)
        event.add("dtstart", start)
        event.add("dtend", end)
        if location:
            event.add("location", location)
        if description:
            event.add("description", description)

        ical.add_component(event)
        cal.save_event(ical.to_ical().decode())

        return f"Event created: {title} on {start.strftime('%Y-%m-%d %H:%M')}"
    except Exception as exc:
        logger.error(f"CalDAV event creation failed: {exc}")
        return f"Failed to create event: {exc}"
