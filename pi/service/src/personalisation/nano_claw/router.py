"""
Mode router — classifies input and dispatches to the right agent.

  creative  → writer.generate()   prose in user's voice (Opus, adaptive thinking)
  cognitive → agent.respond()     tasks, notes, emails, context retrieval (Haiku + tools)

Mode is determined by:
  1. Explicit slash command prefix  (/create /write /alchemist → creative;
                                     /note /todo /email /task /context → cognitive)
  2. Keyword fast-path (no API call)
  3. Haiku classification for ambiguous free-form text
"""

from __future__ import annotations

from typing import Literal

import anthropic

from ..config import settings

Mode = Literal["creative", "cognitive"]

_CREATIVE_COMMANDS = {"/create", "/write", "/alchemist", "/prose"}
_COGNITIVE_COMMANDS = {"/note", "/todo", "/email", "/task", "/context", "/search",
                       "/find", "/remind", "/research", "/draft"}

_CREATIVE_WORDS = {"prose", "poem", "poetry", "lyrics", "story", "narrative", "passage",
                   "chapter", "scene", "write about", "describe", "imagine", "paint", "compose"}
_COGNITIVE_WORDS = {"todo", "task", "remind", "schedule", "email", "note:", "note to",
                    "what is", "how do", "how to", "find me", "look up", "search for",
                    "add to", "list my", "save this", "remember"}


def extract_command(text: str) -> tuple[str | None, str]:
    """Return (command_lower, remaining_text) or (None, original_text)."""
    stripped = text.strip()
    if stripped.startswith("/"):
        parts = stripped.split(None, 1)
        return parts[0].lower(), (parts[1] if len(parts) > 1 else "")
    return None, stripped


def _keyword_classify(text: str) -> Mode | None:
    lower = text.lower()
    if any(kw in lower for kw in _CREATIVE_WORDS):
        return "creative"
    if any(kw in lower for kw in _COGNITIVE_WORDS):
        return "cognitive"
    return None


async def classify(text: str) -> Mode:
    """Classify text as creative or cognitive mode."""
    command, _ = extract_command(text)
    if command in _CREATIVE_COMMANDS:
        return "creative"
    if command in _COGNITIVE_COMMANDS:
        return "cognitive"

    # Fast path: keyword check (no API call)
    fast = _keyword_classify(text)
    if fast:
        return fast

    # Call Haiku to classify
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    resp = await client.messages.create(
        model=settings.nano_claw_model,
        max_tokens=5,
        system=(
            "Classify the user's intent. Reply with exactly one word — 'creative' or 'cognitive'.\n\n"
            "creative: prose, poetry, stories, writing in a personal voice, book content, lyrics, "
            "creative expression, descriptions of scenes or feelings.\n"
            "cognitive: tasks, todos, reminders, emails, notes, questions, searches, summaries, "
            "factual queries, anything practical or informational."
        ),
        messages=[{"role": "user", "content": text[:500]}],
    )
    word = resp.content[0].text.strip().lower()
    return "creative" if word == "creative" else "cognitive"


async def route(user_id: str, text: str, media_context: str | None = None) -> tuple[str, Mode]:
    """
    Classify and dispatch to the correct agent.
    Returns (response_text, mode_used).

    media_context: transcribed audio, image description, etc.
    """
    # Combine text and media context for classification
    classify_input = text
    if media_context and not text:
        classify_input = media_context
    elif media_context and text:
        classify_input = f"{text} {media_context}"

    mode = await classify(classify_input)

    # Strip command prefix before passing to the agent
    _, content = extract_command(classify_input)
    prompt = content or classify_input

    if mode == "creative":
        from ..agents.writer import generate
        result = await generate(user_id, prompt)
    else:
        from . import agent
        result = await agent.respond(user_id, prompt)

    return result, mode
