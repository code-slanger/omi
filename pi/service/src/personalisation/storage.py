import json
from pathlib import Path

import aiofiles

from .config import settings


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _uploads_dir(user_id: str, media_type: str) -> Path:
    p = Path(settings.data_dir) / "uploads" / user_id / media_type
    p.mkdir(parents=True, exist_ok=True)
    return p


def _profiles_dir(user_id: str) -> Path:
    p = Path(settings.data_dir) / "profiles" / user_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _feedback_dir(user_id: str) -> Path:
    p = Path(settings.data_dir) / "feedback" / user_id
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------

async def save_upload(user_id: str, media_type: str, filename: str, content: bytes) -> str:
    path = _uploads_dir(user_id, media_type) / filename
    async with aiofiles.open(path, "wb") as f:
        await f.write(content)
    return str(path)


def list_uploads(user_id: str, media_type: str | None = None) -> list[dict]:
    base = Path(settings.data_dir) / "uploads" / user_id
    if not base.exists():
        return []

    types = [media_type] if media_type else ["text", "audio", "image", "video"]
    results = []
    for mt in types:
        mt_path = base / mt
        if mt_path.exists():
            for f in sorted(mt_path.iterdir()):
                results.append({
                    "path": str(f),
                    "media_type": mt,
                    "filename": f.name,
                    "size_bytes": f.stat().st_size,
                })
    return results


def read_file(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

async def save_profile(user_id: str, profile: dict) -> None:
    path = _profiles_dir(user_id) / "profile.json"
    async with aiofiles.open(path, "w") as f:
        await f.write(json.dumps(profile, indent=2))


async def load_profile(user_id: str) -> dict | None:
    path = _profiles_dir(user_id) / "profile.json"
    if not path.exists():
        return None
    async with aiofiles.open(path, "r") as f:
        return json.loads(await f.read())


# ---------------------------------------------------------------------------
# Generated outputs
# ---------------------------------------------------------------------------

def _outputs_dir(user_id: str) -> Path:
    p = Path(settings.data_dir) / "outputs" / user_id
    p.mkdir(parents=True, exist_ok=True)
    return p


async def save_generation(user_id: str, source: str, transcript: str, text: str, generation_id: str) -> Path:
    """Save a generated piece as a markdown file. Returns the saved path."""
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"{timestamp}_{source}_{generation_id[:8]}.md"
    path = _outputs_dir(user_id) / filename
    content = (
        f"---\ncreated: {datetime.now().isoformat()}\n"
        f"source: {source}\ngeneration_id: {generation_id}\n"
        f"transcript: \"{transcript}\"\n---\n\n{text}\n"
    )
    async with aiofiles.open(path, "w") as f:
        await f.write(content)
    return path


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

async def append_feedback(user_id: str, record: dict) -> None:
    path = _feedback_dir(user_id) / "feedback.jsonl"
    async with aiofiles.open(path, "a") as f:
        await f.write(json.dumps(record) + "\n")
