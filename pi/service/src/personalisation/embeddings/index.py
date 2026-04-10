import hashlib
import os
from typing import Any

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

from ..config import settings

_client: chromadb.ClientAPI | None = None
_ef: SentenceTransformerEmbeddingFunction | None = None


def _get_client() -> chromadb.ClientAPI:
    global _client
    if _client is None:
        path = os.path.join(settings.data_dir, "chroma")
        _client = chromadb.PersistentClient(path=path)
    return _client


def _get_ef() -> SentenceTransformerEmbeddingFunction:
    global _ef
    if _ef is None:
        _ef = SentenceTransformerEmbeddingFunction(model_name=settings.embedding_model)
    return _ef


def _collection_name(user_id: str) -> str:
    # ChromaDB names: 3–63 chars, alphanumeric + hyphens, no leading/trailing hyphens
    safe = "".join(c if c.isalnum() else "-" for c in user_id).strip("-")[:55]
    return f"usr-{safe}"


def add_documents(user_id: str, documents: list[dict[str, Any]]) -> None:
    """
    Upsert pre-processed documents into a user's collection.
    Each document: {"text": str, "metadata": dict}
    """
    if not documents:
        return

    col = _get_client().get_or_create_collection(
        name=_collection_name(user_id),
        embedding_function=_get_ef(),
    )

    ids = [
        hashlib.md5((d["text"] + str(d.get("metadata", {}))).encode()).hexdigest()
        for d in documents
    ]
    texts = [d["text"] for d in documents]
    metadatas = [_flatten(d.get("metadata", {})) for d in documents]

    col.upsert(ids=ids, documents=texts, metadatas=metadatas)


def collection_count(user_id: str) -> int:
    try:
        col = _get_client().get_collection(
            name=_collection_name(user_id),
            embedding_function=_get_ef(),
        )
        return col.count()
    except Exception:
        return 0


def delete_collection(user_id: str) -> None:
    try:
        _get_client().delete_collection(_collection_name(user_id))
    except Exception:
        pass


def _flatten(meta: dict) -> dict:
    """ChromaDB metadata values must be str | int | float | bool."""
    flat: dict[str, str | int | float | bool] = {}
    for k, v in meta.items():
        if isinstance(v, (str, int, float, bool)):
            flat[k] = v
        elif isinstance(v, (list, dict)):
            flat[k] = str(v)
        else:
            flat[k] = str(v)
    return flat
