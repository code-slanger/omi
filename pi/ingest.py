#!/usr/bin/env python3
"""
Corpus ingestion — runs on Pi.
Upload a folder of writing to the service for embedding and indexing.

Usage (SSH into Pi, then):
    python ingest.py /path/to/writing
    python ingest.py ~/obsidian --user-id mitch --source-type own_writing
"""

import argparse
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
import os

load_dotenv()

SERVICE_BASE_URL = os.getenv("SERVICE_BASE_URL", "http://localhost:8000")
DEFAULT_USER_ID = os.getenv("USER_ID", "default")


def ingest(source_path: Path, user_id: str, base_url: str, source_type: str):
    files = list(source_path.rglob("*.md")) + list(source_path.rglob("*.txt"))
    print(f"Found {len(files)} files in {source_path} [{source_type}]")

    if not files:
        print("No .md or .txt files found.")
        return

    total_chunks = 0
    for file in files:
        try:
            with open(file, "rb") as f:
                resp = requests.post(
                    f"{base_url}/users/{user_id}/upload",
                    files={"file": (file.name, f, "text/plain")},
                    params={"source_type": source_type},
                    timeout=30,
                )
            resp.raise_for_status()
            data = resp.json()
            total_chunks += data["chunks_indexed"]
            print(f"  {file.name} → {data['chunks_indexed']} chunks (corpus: {data['corpus_size']})")
        except requests.HTTPError as e:
            print(f"  {file.name} → error {e.response.status_code}: {e.response.text}")
        except Exception as e:
            print(f"  {file.name} → failed: {e}")

    print(f"\nDone. {total_chunks} chunks indexed for user '{user_id}'")


def main():
    parser = argparse.ArgumentParser(description="Upload a writing folder to the personalisation service")
    parser.add_argument("source", help="Path to folder of .md/.txt files")
    parser.add_argument("--user-id", default=DEFAULT_USER_ID, help="User ID (default: $USER_ID or 'default')")
    parser.add_argument("--base-url", default=SERVICE_BASE_URL, help="Service base URL")
    parser.add_argument("--source-type", default="reference", choices=["own_writing", "reference"],
                        help="'own_writing' for your own work, 'reference' for books/notes (default: reference)")
    args = parser.parse_args()

    source_path = Path(args.source).expanduser().resolve()
    if not source_path.exists():
        print(f"Error: not found: {source_path}")
        sys.exit(1)

    ingest(source_path, args.user_id, args.base_url, args.source_type)


if __name__ == "__main__":
    main()
