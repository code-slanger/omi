from typing import Optional

import anthropic
from pydantic import BaseModel

from ..config import settings
from ..embeddings.retrieval import retrieve
from ..storage import save_profile, load_profile


# ---------------------------------------------------------------------------
# Profile schema — used for both structured output and storage
# ---------------------------------------------------------------------------

class TextProfile(BaseModel):
    voice_summary: str
    common_themes: list[str]
    avg_sentence_length: int


class AudioProfile(BaseModel):
    preferred_bpm_range: list[float]
    preferred_keys: list[str]
    sonic_references: list[str]


class VisualProfile(BaseModel):
    palette: list[str]
    recurring_subjects: list[str]


class CreativeProfile(BaseModel):
    """The portion of the profile derived from analysis (no user_id)."""
    text: Optional[TextProfile] = None
    audio: Optional[AudioProfile] = None
    visual: Optional[VisualProfile] = None


class UserProfile(CreativeProfile):
    user_id: str


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

async def build_profile(user_id: str) -> UserProfile:
    """
    Analyse a user's corpus and produce a structured creative profile.
    Uses claude-opus-4-6 with adaptive thinking + structured outputs.
    """
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    own_writing = retrieve(
        user_id, "writing voice style themes mood narrative",
        n_results=15, media_type="text", source_type="own_writing",
    )
    reference = retrieve(
        user_id, "writing voice style themes mood",
        n_results=5, media_type="text", source_type="reference",
    )
    audio_samples = retrieve(user_id, "music tempo BPM key sonic style", n_results=5, media_type="audio")
    visual_samples = retrieve(user_id, "colour palette visual subjects aesthetic", n_results=5, media_type="image")

    corpus = _format_corpus(own_writing, reference, audio_samples, visual_samples)

    if not corpus.strip():
        profile = UserProfile(user_id=user_id)
        await save_profile(user_id, profile.model_dump())
        return profile

    response = await client.messages.parse(
        model=settings.generation_model,
        max_tokens=2048,
        thinking={"type": "adaptive"},
        system=(
            "You are a creative analyst. Study the samples from this creator's corpus and "
            "extract a precise, specific profile of their artistic voice and preferences. "
            "Avoid generic descriptions — every field should reflect what is genuinely distinct "
            "about this creator."
        ),
        messages=[{"role": "user", "content": corpus}],
        output_format=CreativeProfile,
    )

    data = response.parsed_output.model_dump()
    profile = UserProfile(user_id=user_id, **data)
    await save_profile(user_id, profile.model_dump())
    return profile


def _format_corpus(
    own_writing: list[dict],
    reference: list[dict],
    audio_samples: list[dict],
    visual_samples: list[dict],
) -> str:
    parts: list[str] = []

    if own_writing:
        excerpts = "\n---\n".join(s["text"] for s in own_writing)
        parts.append(
            "## The creator's own writing (PRIMARY SOURCE — base your entire voice "
            "analysis on this; all other sections are supplementary)\n" + excerpts
        )

    if reference:
        excerpts = "\n---\n".join(s["text"] for s in reference)
        parts.append(f"## Reference material (books, notes — use only to infer taste and worldview)\n{excerpts}")

    if audio_samples:
        lines = "\n".join(s["text"] for s in audio_samples)
        parts.append(f"## Audio files\n{lines}")

    if visual_samples:
        lines = "\n".join(s["text"] for s in visual_samples)
        parts.append(f"## Visual files\n{lines}")

    return "\n\n".join(parts)
