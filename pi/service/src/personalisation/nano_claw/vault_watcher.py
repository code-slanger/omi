"""
Vault watcher — keeps ChromaDB in sync with the Obsidian vault.
Watches for new/modified .md files and re-indexes them.
Runs as an asyncio background task started in the FastAPI lifespan.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from watchfiles import awatch, Change

from ..config import settings
from ..embeddings.index import add_documents
from ..preprocessors import text as text_prep

logger = logging.getLogger(__name__)


async def watch(user_id: str) -> None:
    """Watch the Obsidian vault and re-index changed files into ChromaDB."""
    vault_path_str = settings.obsidian_vault_path
    if not vault_path_str:
        logger.info("OBSIDIAN_VAULT_PATH not set — vault watcher disabled")
        return

    vault = Path(vault_path_str)
    if not vault.exists():
        logger.warning(f"Vault path does not exist: {vault} — watcher disabled")
        return

    logger.info(f"Vault watcher started: {vault}")

    try:
        async for changes in awatch(str(vault)):
            for change_type, path_str in changes:
                if not path_str.endswith(".md"):
                    continue
                if change_type in (Change.added, Change.modified):
                    await _index_file(Path(path_str), user_id)
    except asyncio.CancelledError:
        logger.info("Vault watcher stopped")
    except Exception as e:
        logger.error(f"Vault watcher error: {e}")


async def _index_file(path: Path, user_id: str) -> None:
    try:
        content = path.read_bytes()
        docs = text_prep.preprocess(content, path.name, source_type="own_writing")
        if docs:
            add_documents(user_id, docs)
            logger.debug(f"Indexed {path.name} ({len(docs)} chunks)")
    except Exception as e:
        logger.error(f"Failed to index {path.name}: {e}")
