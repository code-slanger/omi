"""
Python template renderer — generates Obsidian daily/weekly/monthly notes
without requiring Templater. Mirrors the existing Templater JS templates exactly.

Used by the Telegram bot (and any cron) to create journal scaffolding on the Pi.
Templater still works fine for manual note creation inside Obsidian — this is the
parallel automation path.

Output is drop-in compatible with the existing vault structure:
  Journal/Daily/YYYY-MM-DD.md
  Journal/Weekly/YYYY-Www.md
  Journal/Monthly/YYYY-MM.md
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Date helpers
# ─────────────────────────────────────────────────────────────────────────────

def _iso_week(d: date) -> str:
    """2026-W15"""
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _iso_year(d: date) -> int:
    return d.isocalendar()[0]


def _week_num(d: date) -> int:
    return d.isocalendar()[1]


def _quarter_label(d: date) -> str:
    return f"Q{(d.month - 1) // 3 + 1}"


def _quarter_key(d: date) -> str:
    return f"{d.year}-{_quarter_label(d)}"


def _month_key(d: date) -> str:
    return d.strftime("%Y-%m")


def _month_name(d: date) -> str:
    return d.strftime("%B")


def _monday_of_week(d: date) -> date:
    """Return the Monday of the ISO week containing d."""
    return d - timedelta(days=d.weekday())


def _first_of_next_month(d: date) -> date:
    if d.month == 12:
        return d.replace(year=d.year + 1, month=1, day=1)
    return d.replace(month=d.month + 1, day=1)


def _fetch_quote() -> str:
    """Fetch a random inspirational quote from zenquotes.io."""
    try:
        import httpx
        resp = httpx.get("https://zenquotes.io/api/random", timeout=5)
        data = resp.json()
        return f'> {data[0]["q"]}\n> — {data[0]["a"]}'
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Daily note
# ─────────────────────────────────────────────────────────────────────────────

def render_daily(
    d: date,
    fetch_quote: bool = True,
    mood: str = "",
    meditation: bool | None = None,
    exercise: bool | None = None,
    read: bool | None = None,
    side_hustle_hours: str = "",
    money_spent: str = "",
    grateful: str = "",
    highlight: str = "",
) -> str:
    """
    Render a daily note for date `d`.
    The output is static markdown — all Dataview/Todoist blocks are included
    verbatim and will be rendered by their respective Obsidian plugins.

    Optional pre-fill values (from Telegram conversation) are injected into
    the frontmatter and body so the note opens with answers already in place.
    """
    date_str = d.strftime("%Y-%m-%d")
    week_str = _iso_week(d)
    weekday = d.strftime("%A")
    quarter_key = _quarter_key(d)
    quarter_label = _quarter_label(d)
    month_key = _month_key(d)
    month_name = _month_name(d)
    week_label = f"Week {_week_num(d)}"

    prev_date = (d - timedelta(days=1)).strftime("%Y-%m-%d")  # ISO — used as link target
    next_date = (d + timedelta(days=1)).strftime("%Y-%m-%d")  # ISO — used as link target
    display_date = d.strftime("%d/%m/%y")

    quote_block = ""
    if fetch_quote:
        q = _fetch_quote()
        if q:
            quote_block = f"{q}\n\n"

    def _bool_val(v: bool | None) -> str:
        if v is True:
            return "true"
        if v is False:
            return "false"
        return ""

    return f"""\
---
week: {week_str}
weekday: {weekday}
aliases:
mood: {mood}
tag:
  - Daily
meditation: {_bool_val(meditation)}
exercise: {_bool_val(exercise)}
read: {_bool_val(read)}
sideHustleHours: {side_hustle_hours}
moneySpent: {money_spent}
date: {date_str}
type: daily
source: bot
---
# {weekday}, {d.strftime("%d/%m/%y")}
[[{d.year}]] / [[{quarter_key}|{quarter_label}]] / [[{month_key}|{month_name}]] / [[{week_str}|{week_label}]]

❮ [[{prev_date}]] | {display_date} | [[{next_date}]] ❯

```dataview
table without id
	mood + " #_/habits" AS "🌄",
	choice(meditation,"✅","❌") AS "🧘‍♂️",
	choice(exercise,"✅","❌") AS "🏃‍♂️",
	choice(read,"✅","❌") AS "📚",
	sideHustleHours AS "🧠 Hours",
	moneySpent AS "💰 Spent"
FROM "Journal/Daily"
where file.name = "{date_str}"
```
---
{quote_block}---
![[Journal/Weekly/{week_str}#Goals for this week:]]

---
> [!todo]- Tasks of the day
>```todoist
>name: ''
>filter: "today | overdue"
>sorting:
>- date
>- priority
>group: true
>```
---


## What am I grateful for?
1. Gratitude:: {grateful}

## Highlights of the day:
1. Highlight:: {highlight}

## What did I learn today?
1. Learning::

## What excited or drained me?
1. Exciting::
2. Draining::

## How did I advance towards my goals today?
1. Goal::

## What did I dream about?
1. Dream::

> [!note]- Files created on this day
>```dataview
>LIST WHERE file.cday = date(this.file.name)
>```
"""


def write_daily(vault: Path, d: date, overwrite: bool = False, **kwargs) -> tuple[Path, bool]:
    """
    Write the daily note to vault/Journal/Daily/YYYY-MM-DD.md.
    Returns (path, created) — created=False if it already existed.
    Keyword args are forwarded to render_daily (mood, meditation, exercise,
    read, side_hustle_hours, money_spent, grateful, highlight).
    """
    note_dir = vault / "Journal" / "Daily"
    note_dir.mkdir(parents=True, exist_ok=True)
    note_path = note_dir / f"{d.strftime('%Y-%m-%d')}.md"
    if note_path.exists() and not overwrite:
        logger.info(f"Daily note already exists: {note_path}")
        return note_path, False
    note_path.write_text(render_daily(d, **kwargs), encoding="utf-8")
    logger.info(f"Daily note created: {note_path}")
    return note_path, True


# ─────────────────────────────────────────────────────────────────────────────
# Weekly note
# ─────────────────────────────────────────────────────────────────────────────

def render_weekly(monday: date, goals: list[str] | None = None) -> str:
    """
    Render a weekly note for the ISO week whose Monday is `monday`.
    Handles cross-month and cross-year weeks in the breadcrumb.
    Pass `goals` (up to 3 strings) to pre-fill the weekly goals checklist.
    """
    sunday = monday + timedelta(days=6)
    iso_year = _iso_year(monday)
    week_num = _week_num(monday)
    week_str = f"{iso_year}-W{week_num:02d}"
    week_label = f"Week {week_num}"

    # ── Breadcrumb (handles cross-month/quarter/year) ──────────────────────
    breadcrumb = (
        f"[[{monday.year}]] / "
        f"[[{_quarter_key(monday)}|{_quarter_label(monday)}]] / "
        f"[[{_month_key(monday)}|{_month_name(monday)}]]"
    )
    if monday.month != sunday.month:
        breadcrumb += " - "
        if monday.year != sunday.year:
            breadcrumb += f"[[{sunday.year}]] / "
        if _quarter_label(monday) != _quarter_label(sunday):
            breadcrumb += f"[[{_quarter_key(sunday)}|{_quarter_label(sunday)}]] / "
        breadcrumb += f"[[{_month_key(sunday)}|{_month_name(sunday)}]]"

    # ── Prev / next links ─────────────────────────────────────────────────
    prev_monday = monday - timedelta(weeks=1)
    next_monday = monday + timedelta(weeks=1)
    prev_str = f"{_iso_year(prev_monday)}-W{_week_num(prev_monday):02d}"
    next_str = f"{_iso_year(next_monday)}-W{_week_num(next_monday):02d}"
    prev_label = f"Week {_week_num(prev_monday)}"
    next_label = f"Week {_week_num(next_monday)}"

    # ── Day links ─────────────────────────────────────────────────────────
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    day_links = " - ".join(
        f"[[{(monday + timedelta(days=i)).strftime('%Y-%m-%d')}|{day_names[i]}]]"
        for i in range(7)
    )

    date_header = f"{monday.strftime('%d/%m/%y')} - {sunday.strftime('%d/%m/%y')}"
    goals_month = _month_key(monday)

    # Build goals checklist — pre-fill from provided list, blank lines for the rest
    goal_lines: list[str] = []
    for i in range(3):
        text = goals[i].strip() if goals and i < len(goals) else ""
        goal_lines.append(f"- [ ] {text}")
    goals_block = "\n".join(goal_lines)

    return f"""\
---
Location:
Tags: weeklyreviews
Aliases:
Enjoyment:
Date: {monday.strftime('%Y-%m-%d')}
type: weekly
source: bot
---
# {iso_year} {week_label}
{breadcrumb}

❮ [[{prev_str}|{prev_label}]] | {week_label} | [[{next_str}|{next_label}]] ❯
{day_links}
# {date_header}
---
![[Journal/Monthly/{goals_month}#Goals for this month^]]
---
## Goals for this week:
{goals_block}
---
> [!todo]- Tasks
>```todoist
>name: "Tasks"
>filter: "(overdue | today | no date)"
>sorting:
>  - date
>  - priority
>group: true
>```
---
## Overview
```dataview
table without id
	file.link AS "Date",
	mood + " #_/habits" AS "🌄",
	sleep AS "🛌",
	choice(meditation, "✅", "❌") AS "🧘‍♂️",
	choice(exercise, "✅", "❌") AS "🏃‍♂️",
	choice(read, "✅", "❌") AS "📚",
	sideHustleHours AS "🏋️‍♀️ Hours",
	moneySpent AS "💰 Spent"
from "Journal/Daily"
where week = "{week_str}"
sort file.name ASC
```
```dataview
table
	sum(sideHustleHours) AS "🏋️‍♀️ Total Hours",
	sum(moneySpent) AS "💰 Total Spent"
from "Journal/Daily"
where week = "{week_str}"
```


## What is worth remembering about this week?


## What did I accomplish this week?


## What could I have done better this week?


## What am I grateful for this week, and what am I thinking of?


## How did I advance towards my goals this week?
"""


def write_weekly(
    vault: Path,
    monday: date,
    overwrite: bool = False,
    goals: list[str] | None = None,
) -> tuple[Path, bool]:
    """
    Write the weekly note to vault/Journal/Weekly/YYYY-Www.md.
    Pass `goals` to pre-fill the goals checklist.
    """
    iso_year = _iso_year(monday)
    week_num = _week_num(monday)
    week_str = f"{iso_year}-W{week_num:02d}"
    note_dir = vault / "Journal" / "Weekly"
    note_dir.mkdir(parents=True, exist_ok=True)
    note_path = note_dir / f"{week_str}.md"
    if note_path.exists() and not overwrite:
        logger.info(f"Weekly note already exists: {note_path}")
        return note_path, False
    note_path.write_text(render_weekly(monday, goals=goals), encoding="utf-8")
    logger.info(f"Weekly note created: {note_path}")
    return note_path, True


# ─────────────────────────────────────────────────────────────────────────────
# Monthly note
# ─────────────────────────────────────────────────────────────────────────────

def render_monthly(d: date) -> str:
    """Render a monthly note for the month of `d`."""
    first = d.replace(day=1)
    next_first = _first_of_next_month(first)
    prev_last = first - timedelta(days=1)

    month_name = _month_name(d)
    prev_month_key = _month_key(prev_last)
    prev_month_name = _month_name(prev_last)
    next_month_key = _month_key(next_first)
    next_month_name = _month_name(next_first)
    quarter_key = _quarter_key(d)
    quarter_label = _quarter_label(d)

    return f"""\
---
Date: {first.strftime('%Y-%m-%d')}
Tags: monthlyreviews
type: monthly
source: bot
---
# {month_name} {d.year}
[[{d.year}]] / [[{quarter_key}|{quarter_label}]]

❮ [[{prev_month_key}|{prev_month_name}]] | {month_name} | [[{next_month_key}|{next_month_name}]] ❯

---
## Goals for this month:
- [ ]
- [ ]
- [ ]
---
## Overview
```dataview
table without id
	file.link AS "Week",
	Enjoyment AS "😊"
from "Journal/Weekly"
where Date >= date("{first.strftime('%Y-%m-%d')}") and Date < date("{next_first.strftime('%Y-%m-%d')}")
sort file.name ASC
```

## What did I accomplish this month?


## What could I have done better this month?


## What am I grateful for this month?


## How did I advance towards my goals this month?
"""


def write_monthly(vault: Path, d: date, overwrite: bool = False) -> tuple[Path, bool]:
    """Write the monthly note to vault/Journal/Monthly/YYYY-MM.md."""
    month_key = _month_key(d)
    note_dir = vault / "Journal" / "Monthly"
    note_dir.mkdir(parents=True, exist_ok=True)
    note_path = note_dir / f"{month_key}.md"
    if note_path.exists() and not overwrite:
        logger.info(f"Monthly note already exists: {note_path}")
        return note_path, False
    note_path.write_text(render_monthly(d), encoding="utf-8")
    logger.info(f"Monthly note created: {note_path}")
    return note_path, True


# ─────────────────────────────────────────────────────────────────────────────
# Event note (Full Calendar compatible)
# ─────────────────────────────────────────────────────────────────────────────

def render_event(
    title: str,
    event_date: date,
    start_time: str = "",
    end_time: str = "",
    location: str = "",
    description: str = "",
    all_day: bool = True,
) -> str:
    """
    Render an event note compatible with the Obsidian Full Calendar plugin.
    Frontmatter matches exactly what Full Calendar creates when you make an event
    manually — so notes are fully editable from within the plugin.

    Notes go in Journal/ (the directory Full Calendar watches).
    """
    date_str = event_date.strftime("%Y-%m-%d")  # ISO — used in frontmatter for Full Calendar
    weekday = event_date.strftime("%A")
    human_date = f"{weekday}, {event_date.strftime('%d/%m/%y')}"

    # Build frontmatter to match Full Calendar's schema exactly
    fm_lines = [
        "---",
        f"title: {title}",
        f"allDay: {'true' if all_day else 'false'}",
    ]
    if not all_day and start_time:
        fm_lines.append(f"startTime: {start_time}")
    if not all_day and end_time:
        fm_lines.append(f"endTime: {end_time}")
    fm_lines.append(f"date: {date_str}")
    fm_lines.append("completed: null")
    if location:
        fm_lines.append(f"location: {location}")
    fm_lines += ["type: event", "source: bot", "---"]
    frontmatter = "\n".join(fm_lines)

    # Human-readable body (not parsed by Full Calendar — for Obsidian reading)
    body_parts = [f"\n## {title}\n"]
    body_parts.append(f"**Date:** {human_date}")
    if not all_day and start_time:
        time_str = f"{start_time}–{end_time}" if end_time else start_time
        body_parts.append(f"**Time:** {time_str}")
    if location:
        body_parts.append(f"**Location:** {location}")
    if description:
        body_parts.append(f"\n{description}")
    body_parts.append("\n### Notes\n")

    return frontmatter + "\n" + "\n".join(body_parts)


def write_event(
    vault: Path,
    title: str,
    event_date: date,
    start_time: str = "",
    end_time: str = "",
    location: str = "",
    description: str = "",
    all_day: bool = True,
    overwrite: bool = False,
) -> tuple[Path, bool]:
    """
    Write an event note to vault/Journal/YYYY-MM-DD Title.md.
    Full Calendar picks it up automatically from the Journal directory.
    """
    safe_title = "".join(c if c.isalnum() or c in " -_'" else "" for c in title).strip()
    filename = f"{event_date.strftime('%Y-%m-%d')} {safe_title}.md"
    note_dir = vault / "Journal"
    note_dir.mkdir(parents=True, exist_ok=True)
    note_path = note_dir / filename

    if note_path.exists() and not overwrite:
        logger.info(f"Event note already exists: {note_path}")
        return note_path, False

    content = render_event(title, event_date, start_time, end_time, location, description, all_day)
    note_path.write_text(content, encoding="utf-8")
    logger.info(f"Event note created: {note_path}")
    return note_path, True


# ─────────────────────────────────────────────────────────────────────────────
# Book note
# ─────────────────────────────────────────────────────────────────────────────

# Maps Google Books API category strings → vault subfolder names.
# Google often returns compound strings like "Fiction / Science Fiction" — we
# match on keywords so the most-specific known genre wins.
_GENRE_MAP: list[tuple[str, str]] = [
    ("fiction", "Fiction"),
    ("biography", "Biography & Autobiography"),
    ("autobiography", "Biography & Autobiography"),
    ("self-help", "Self-Help"),
    ("self help", "Self-Help"),
    ("health", "Health & Fitness"),
    ("fitness", "Health & Fitness"),
    ("sport", "Health & Fitness"),
    ("business", "Business & Economics"),
    ("economics", "Business & Economics"),
    ("finance", "Business & Economics"),
    ("psychology", "Psychology"),
    ("mind", "Body, Mind & Spirit"),
    ("spirit", "Body, Mind & Spirit"),
    ("religion", "Religion"),
    ("philosophy", "Philosophy"),
    ("science", "Science"),
    ("history", "History"),
    ("political", "Political Science"),
    ("politics", "Political Science"),
    ("social", "Social Science"),
    ("sociology", "Social Science"),
    ("music", "Music"),
    ("true crime", "True Crime"),
    ("crime", "True Crime"),
    ("computers", "Computers"),
    ("technology", "Computers"),
    ("programming", "Computers"),
]


def _resolve_genre(categories: str | list) -> str:
    """Map a Google Books category string to a vault subfolder name."""
    if isinstance(categories, list):
        raw = " ".join(categories).lower()
    else:
        raw = (categories or "").lower()

    for keyword, folder in _GENRE_MAP:
        if keyword in raw:
            return folder

    return "Self-Help"  # sensible catch-all


def _google_books_search(query: str) -> dict | None:
    """Search Google Books and return the best matching volume's info."""
    try:
        import httpx
        from .config import settings as _settings
        params: dict = {"q": query, "maxResults": 1, "printType": "books"}
        if _settings.google_books_api_key:
            params["key"] = _settings.google_books_api_key
        resp = httpx.get(
            "https://www.googleapis.com/books/v1/volumes",
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            return None
        return items[0].get("volumeInfo", {})
    except Exception as exc:
        logger.warning(f"Google Books search failed: {exc}")
        return None


def render_book(
    title: str,
    author: str,
    categories: str,
    publisher: str = "",
    publish_date: str = "",
    pages: int = 0,
    isbn10: str = "",
    isbn13: str = "",
    cover_url: str = "",
    description: str = "",
) -> str:
    """
    Render a book note matching the existing Book.md template format exactly
    (Templater syntax removed — this is the static rendered version).
    """
    from datetime import date as _date
    today = _date.today().strftime("%Y-%m-%d")

    # Build the Google Books high-res cover URL (same logic as the template)
    cover_hires = ""
    if cover_url:
        # Extract the ID from the cover URL
        import re
        m = re.search(r"id=([^&]+)", cover_url)
        if m:
            book_id = m.group(1)
            cover_hires = f"https://books.google.com/books/publisher/content/images/frontcover/{book_id}?fife=w600-h900&source=gbs_api"
        else:
            cover_hires = cover_url

    isbn_str = f"{isbn10} {isbn13}".strip()

    return f"""\
---
title: "{title}"
author: {author}
series:
seriesnumber:
categories: {categories}
rating:
readdates:
- started:
  finished:
shelf: toRead
list:
publisher: {publisher}
publish: {publish_date}
pages: {pages}
isbn: {isbn_str}
cover: {cover_hires}
dateCreated: {today}
type: book
source: bot
---

![cover|150]({cover_url})

## {title}

### Description

{description}

### Notes
"""


def write_book(
    vault: Path,
    query: str,
) -> tuple[Path, bool, str]:
    """
    Search Google Books, render a note, and save it to the correct genre folder.
    Returns (path, was_created, summary_message).
    """
    info = _google_books_search(query)
    if not info:
        return vault / "unknown.md", False, f"No results found for '{query}' on Google Books."

    title = info.get("title", query)
    authors = info.get("authors", [])
    author = ", ".join(authors)
    raw_categories = info.get("categories", [])
    categories_str = raw_categories[0] if raw_categories else "General"
    publisher = info.get("publisher", "")
    publish_date = info.get("publishedDate", "")
    pages = info.get("pageCount", 0)
    description = info.get("description", "")[:2000]

    # ISBNs
    isbn10, isbn13 = "", ""
    for id_obj in info.get("industryIdentifiers", []):
        if id_obj["type"] == "ISBN_10":
            isbn10 = id_obj["identifier"]
        elif id_obj["type"] == "ISBN_13":
            isbn13 = id_obj["identifier"]

    # Cover URL (thumbnail from Google)
    image_links = info.get("imageLinks", {})
    cover_url = image_links.get("thumbnail", image_links.get("smallThumbnail", ""))

    genre_folder = _resolve_genre(raw_categories)
    safe_title = "".join(c if c.isalnum() or c in " -_'" else "" for c in title).strip()
    safe_author = "".join(c if c.isalnum() or c in " -_'," else "" for c in author).strip()
    filename = f"{safe_title} - {safe_author}.md" if safe_author else f"{safe_title}.md"

    note_dir = vault / "Art" / "Books" / "List" / genre_folder
    note_dir.mkdir(parents=True, exist_ok=True)
    note_path = note_dir / filename

    if note_path.exists():
        return note_path, False, f"Book already in vault: _{title}_ by {author}"

    content = render_book(
        title=title,
        author=author,
        categories=categories_str,
        publisher=publisher,
        publish_date=publish_date,
        pages=pages,
        isbn10=isbn10,
        isbn13=isbn13,
        cover_url=cover_url,
        description=description,
    )
    note_path.write_text(content, encoding="utf-8")
    logger.info(f"Book note created: {note_path}")

    summary = (
        f"Added _{title}_ by {author}\n"
        f"Genre: {genre_folder}\n"
        f"Saved to: Art/Books/List/{genre_folder}/{filename}"
    )
    return note_path, True, summary


# ─────────────────────────────────────────────────────────────────────────────
# Convenience
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Vision capture note
# ─────────────────────────────────────────────────────────────────────────────

def write_capture_note(
    vault: Path,
    title: str,
    content: str,
    capture_type: str,
    timestamp: datetime | None = None,
    image_filename: str = "",
) -> tuple[Path, bool]:
    """
    Write a vision capture note to vault/Captures/YYYY-MM-DD/HH-MM title.md.

    Args:
        vault:          Root of the Obsidian vault.
        title:          Note title (from vision AI).
        content:        Markdown-formatted extracted text.
        capture_type:   "handwriting" | "printed" | "mixed"
        timestamp:      Capture time (UTC); defaults to now.
        image_filename: Optional reference to the saved JPEG.

    Returns:
        (path, was_created)
    """
    ts = timestamp or datetime.now()
    date_str = ts.strftime("%Y-%m-%d")
    time_str = ts.strftime("%H-%M")

    safe_title = "".join(c if c.isalnum() or c in " -_'" else "" for c in title).strip()
    if not safe_title:
        safe_title = "Capture"

    filename = f"{time_str} {safe_title}.md"
    note_dir = vault / "Captures" / date_str
    note_dir.mkdir(parents=True, exist_ok=True)
    note_path = note_dir / filename

    if note_path.exists():
        logger.info("Capture note already exists: %s", note_path)
        return note_path, False

    tag_type = capture_type if capture_type != "none" else "capture"
    frontmatter_lines = [
        "---",
        f"date: {ts.strftime('%Y-%m-%d')}",
        f"time: \"{ts.strftime('%H:%M')}\"",
        "type: capture",
        f"capture_type: {capture_type}",
        "source: omi-glasses",
        "tags:",
        "  - capture",
        f"  - {tag_type}",
    ]
    if image_filename:
        frontmatter_lines.append(f"image: {image_filename}")
    frontmatter_lines.append("---")

    note_content = "\n".join(frontmatter_lines) + "\n\n" + content + "\n"
    note_path.write_text(note_content, encoding="utf-8")
    logger.info("Capture note written: %s", note_path)
    return note_path, True


def ensure_today(vault: Path) -> dict[str, tuple[Path, bool]]:
    """
    Create today's daily, this week's weekly, and this month's monthly notes
    if they don't already exist. Returns {type: (path, was_created)}.
    """
    today = date.today()
    monday = _monday_of_week(today)
    return {
        "daily": write_daily(vault, today),
        "weekly": write_weekly(vault, monday),
        "monthly": write_monthly(vault, today),
    }
