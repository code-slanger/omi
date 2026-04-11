"""
Direct vault command handlers — no AI involved.

Called from both the voice pipeline (main.py) and Telegram slash commands.
Reads Obsidian vault files directly and returns formatted strings.

Add a new command:
  1. Write a handler function that returns a str.
  2. Add an entry to COMMANDS: name → (handler, [voice trigger phrases]).
  3. Register the Telegram slash command in telegram.py.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..config import settings


# ---------------------------------------------------------------------------
# Vault helpers
# ---------------------------------------------------------------------------

def _vault() -> Path | None:
    if not settings.obsidian_vault_path:
        return None
    p = Path(settings.obsidian_vault_path)
    return p if p.exists() else None


def _read_note(filename: str) -> str | None:
    vault = _vault()
    if vault is None:
        return None
    path = vault / filename
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def get_todos() -> str:
    """Return all open tasks from Tasks.md."""
    text = _read_note("Tasks.md")
    if text is None:
        return "Tasks.md not found in vault."

    # Extract unchecked task lines: - [ ] ...
    tasks = [
        line.strip()
        for line in text.splitlines()
        if re.match(r"^\s*-\s*\[\s*\]", line)
    ]
    if not tasks:
        return "No open tasks."
    return "Open tasks:\n" + "\n".join(tasks)


def get_shopping() -> str:
    """Return all items from Shopping.md."""
    text = _read_note("Shopping.md")
    if text is None:
        return "Shopping.md not found in vault."

    # Return all non-empty non-header lines
    items = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not items:
        return "Shopping list is empty."
    return "Shopping list:\n" + "\n".join(items)


def get_note(filename: str) -> str:
    """Return the raw content of any vault note."""
    if not filename.endswith(".md"):
        filename += ".md"
    text = _read_note(filename)
    if text is None:
        return f"{filename} not found in vault."
    return text.strip() or f"{filename} is empty."


# ---------------------------------------------------------------------------
# Command registry
# ---------------------------------------------------------------------------
# Maps command name → (handler, voice trigger phrases).
# Voice triggers are checked as substrings of the cleaned transcript (lowercase).

COMMANDS: dict[str, tuple] = {
    "todos": (
        get_todos,
        [
            "todo list",
            "to do list",
            "my todos",
            "my tasks",
            "task list",
            "get tasks",
            "show tasks",
            "what are my tasks",
            "what do i need to do",
        ],
    ),
    "shopping": (
        get_shopping,
        [
            "shopping list",
            "my shopping",
            "get shopping",
            "show shopping",
            "what's on my shopping",
            "whats on my shopping",
        ],
    ),
}


def match_voice_command(text: str) -> str | None:
    """
    Check whether `text` matches a known voice command.
    Returns the command name if matched, else None.
    """
    lower = text.lower()
    for name, (_, triggers) in COMMANDS.items():
        if any(t in lower for t in triggers):
            return name
    return None


def run(command_name: str) -> str:
    """Execute a command by name and return the result string."""
    entry = COMMANDS.get(command_name)
    if not entry:
        return f"Unknown command: {command_name}"
    handler, _ = entry
    return handler()
