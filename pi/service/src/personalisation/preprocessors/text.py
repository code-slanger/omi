import re
from typing import Any

CHUNK_SIZE = 400   # words per chunk
CHUNK_OVERLAP = 50


def preprocess(content: bytes, filename: str, source_type: str = "reference") -> list[dict[str, Any]]:
    """
    Parse raw text bytes into overlapping word-window chunks.
    Returns list of {"text": str, "metadata": dict}.
    """
    text = content.decode("utf-8", errors="replace")
    text = _clean(text)

    words = text.split()
    if not words:
        return []

    chunks = []
    start = 0
    chunk_index = 0
    while start < len(words):
        end = min(start + CHUNK_SIZE, len(words))
        chunk_text = " ".join(words[start:end])
        chunks.append({
            "text": chunk_text,
            "metadata": {
                "filename": filename,
                "media_type": "text",
                "source_type": source_type,
                "chunk_index": chunk_index,
                "word_count": end - start,
            },
        })
        chunk_index += 1
        if end == len(words):
            break
        start += CHUNK_SIZE - CHUNK_OVERLAP

    return chunks


def _clean(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()
