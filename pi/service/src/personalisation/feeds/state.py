"""
Feed state — tracks what has already been seen to avoid duplicates.

Stored at DATA_DIR/feed_state.json. Thread-safe via a module-level lock.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from ..config import settings

logger = logging.getLogger(__name__)
_lock = asyncio.Lock()


def _state_path() -> Path:
    return Path(settings.data_dir) / "feed_state.json"


def _load_raw() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _save_raw(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


async def get(section: str, key: str, field: str) -> str | None:
    """Return the stored value for state[section][key][field]."""
    async with _lock:
        state = _load_raw()
        return state.get(section, {}).get(key, {}).get(field)


async def set_value(section: str, key: str, field: str, value: str) -> None:
    """Persist state[section][key][field] = value."""
    async with _lock:
        st = _load_raw()
        st.setdefault(section, {}).setdefault(key, {})[field] = value
        _save_raw(st)


async def get_seen_guids(section: str, key: str) -> set[str]:
    """Return set of seen item GUIDs for RSS/Substack sources."""
    async with _lock:
        state = _load_raw()
        return set(state.get(section, {}).get(key, {}).get("seen_guids", []))


async def mark_guids_seen(section: str, key: str, guids: list[str], max_keep: int = 200) -> None:
    """Add GUIDs to the seen set, capping history to max_keep."""
    async with _lock:
        state = _load_raw()
        existing: list[str] = state.setdefault(section, {}).setdefault(key, {}).setdefault("seen_guids", [])
        combined = list(dict.fromkeys(existing + guids))  # preserve order, dedupe
        state[section][key]["seen_guids"] = combined[-max_keep:]
        _save_raw(state)
