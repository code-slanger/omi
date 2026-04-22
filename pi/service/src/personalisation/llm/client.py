"""
Multi-LLM client abstraction.

get_client(role) returns the right provider for the task:
  "creative"   → Anthropic Opus  (best quality for creative writing)
  "cognitive"  → Anthropic Haiku (fast tool-use agent)
  "classifier" → Anthropic Haiku (fast, cheap classification)
  "summarize"  → Gemini 1.5 Pro if configured (long context); else Haiku
  "local"      → Ollama if configured; else Haiku

All providers expose the same interface:
  await provider.complete(system, user, max_tokens=1024) -> str
"""
from __future__ import annotations

import asyncio
import logging
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class LLMProvider(Protocol):
    async def complete(self, system: str, user: str, max_tokens: int = 1024) -> str:
        ...


class AnthropicProvider:
    def __init__(self, model: str):
        self.model = model

    async def complete(self, system: str, user: str, max_tokens: int = 1024) -> str:
        import anthropic
        from ..config import settings
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        resp = await client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return resp.content[0].text


class GeminiProvider:
    """Google Gemini — best for very long context (transcripts, long docs)."""

    def __init__(self, model: str = "gemini-1.5-pro"):
        self.model = model

    async def complete(self, system: str, user: str, max_tokens: int = 4096) -> str:
        import google.generativeai as genai
        from ..config import settings
        genai.configure(api_key=settings.gemini_api_key)
        gm = genai.GenerativeModel(
            self.model,
            system_instruction=system,
            generation_config={"max_output_tokens": max_tokens},
        )
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(None, gm.generate_content, user)
        return resp.text


class OllamaProvider:
    """Local Ollama — no API cost, runs on Pi or separate server."""

    def __init__(self, model: str = ""):
        from ..config import settings
        self.model = model or settings.ollama_model
        self.base_url = settings.ollama_base_url

    async def complete(self, system: str, user: str, max_tokens: int = 1024) -> str:
        import httpx
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        async with httpx.AsyncClient(base_url=self.base_url, timeout=120) as client:
            resp = await client.post("/api/chat", json=payload)
            resp.raise_for_status()
            return resp.json()["message"]["content"]


def get_client(role: str) -> LLMProvider:
    """Return the appropriate LLM provider for the given role."""
    from ..config import settings

    if role == "summarize" and settings.ollama_base_url:
        return OllamaProvider()

    if role == "summarize" and settings.gemini_api_key:
        return GeminiProvider()

    if role == "local" and settings.ollama_base_url:
        return OllamaProvider()

    model_map: dict[str, str] = {
        "creative": settings.generation_model,
        "cognitive": settings.nano_claw_model,
        "classifier": settings.nano_claw_model,
        "summarize": settings.nano_claw_model,
        "local": settings.nano_claw_model,
    }
    return AnthropicProvider(model=model_map.get(role, settings.nano_claw_model))
