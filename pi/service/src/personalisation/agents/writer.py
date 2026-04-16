import anthropic

from corpus.embeddings.retrieval import retrieve
from corpus.storage import load_profile

from ..config import settings


async def generate(user_id: str, prompt: str) -> str:
    """
    Generate personalised text grounded in the user's creative voice.
    Retrieves relevant corpus context and loads the user's profile,
    then streams from claude-opus-4-6 with adaptive thinking.
    """
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    context_docs = retrieve(user_id, prompt, n_results=5)
    profile = await load_profile(user_id)
    system_prompt = _build_system(profile, context_docs)

    output = ""
    async with client.messages.stream(
        model=settings.generation_model,
        max_tokens=4096,
        thinking={"type": "adaptive"},
        system=system_prompt,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        async for chunk in stream.text_stream:
            output += chunk

    return output


def _build_system(profile: dict | None, context_docs: list[dict]) -> str:
    lines = [
        "You are a creative writing assistant. Your sole job is to generate content "
        "that sounds authentically like this specific creator — not generic AI output.",
        "",
    ]

    if profile:
        text_p = profile.get("text") or {}
        audio_p = profile.get("audio") or {}
        visual_p = profile.get("visual") or {}

        if text_p.get("voice_summary"):
            lines.append(f"**Voice:** {text_p['voice_summary']}")
        if text_p.get("common_themes"):
            lines.append(f"**Themes:** {', '.join(text_p['common_themes'])}")
        if audio_p.get("sonic_references"):
            lines.append(f"**Sonic world:** {', '.join(audio_p['sonic_references'])}")
        if visual_p.get("palette") or visual_p.get("recurring_subjects"):
            combined = (visual_p.get("palette") or []) + (visual_p.get("recurring_subjects") or [])
            lines.append(f"**Visual palette:** {', '.join(combined)}")
        lines.append("")

    if context_docs:
        lines.append("**Samples from their existing work — calibrate your voice to these:**")
        for doc in context_docs:
            if doc["text"]:
                lines.append(f"---\n{doc['text']}")
        lines.append("---\n")

    lines += [
        "Write in their voice. Match their rhythm, vocabulary, and aesthetic.",
        "Output only the requested content — no preamble, no explanation.",
    ]

    return "\n".join(lines)
