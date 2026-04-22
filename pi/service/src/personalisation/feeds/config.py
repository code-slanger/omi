"""
Feed configuration — loads feeds.yaml from DATA_DIR.

Edit DATA_DIR/feeds.yaml to add/remove sources. The scheduler reloads
this file before each daily digest run, so changes take effect immediately.

Schema:
  digest_time: "08:00"   # UTC, 24h format

  rss:
    - url: "https://example.com/feed.xml"
      name: "Source Name"
      summarize: true
      max_items: 5

  youtube:
    - channel_id: "UCxxxxxx"
      name: "Channel Name"
      max_videos: 2
      transcribe: true    # download audio + Whisper + summarize
      summarize_only: false

  substack:
    - url: "https://author.substack.com/feed"
      name: "Author Name"
      summarize: true
      max_items: 3

  github:
    - repo: "owner/repo"
      watch: [commits, pulls, releases]

  email:
    folders: ["INBOX"]
    max_emails: 20
    summarize: true
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..config import settings


@dataclass
class RSSSource:
    url: str
    name: str = ""
    summarize: bool = True
    max_items: int = 5


@dataclass
class YouTubeSource:
    channel_id: str
    name: str = ""
    max_videos: int = 2
    transcribe: bool = True
    summarize_only: bool = False


@dataclass
class SubstackSource:
    url: str
    name: str = ""
    summarize: bool = True
    max_items: int = 3


@dataclass
class GitHubSource:
    repo: str
    watch: list[str] = field(default_factory=lambda: ["commits", "pulls", "releases"])


@dataclass
class EmailConfig:
    folders: list[str] = field(default_factory=lambda: ["INBOX"])
    max_emails: int = 20
    summarize: bool = True


@dataclass
class FeedsConfig:
    digest_time: str = "08:00"
    rss: list[RSSSource] = field(default_factory=list)
    youtube: list[YouTubeSource] = field(default_factory=list)
    substack: list[SubstackSource] = field(default_factory=list)
    github: list[GitHubSource] = field(default_factory=list)
    email: EmailConfig = field(default_factory=EmailConfig)


def _feeds_path() -> Path:
    return Path(settings.data_dir) / "feeds.yaml"


def load() -> FeedsConfig:
    """Load and parse feeds.yaml. Returns empty config if file missing."""
    path = _feeds_path()
    if not path.exists():
        return FeedsConfig()

    with path.open() as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    cfg = FeedsConfig()
    cfg.digest_time = raw.get("digest_time", "08:00")

    for item in raw.get("rss") or []:
        cfg.rss.append(RSSSource(**{k: v for k, v in item.items() if k in RSSSource.__dataclass_fields__}))

    for item in raw.get("youtube") or []:
        cfg.youtube.append(YouTubeSource(**{k: v for k, v in item.items() if k in YouTubeSource.__dataclass_fields__}))

    for item in raw.get("substack") or []:
        cfg.substack.append(SubstackSource(**{k: v for k, v in item.items() if k in SubstackSource.__dataclass_fields__}))

    for item in raw.get("github") or []:
        cfg.github.append(GitHubSource(**{k: v for k, v in item.items() if k in GitHubSource.__dataclass_fields__}))

    if email_raw := raw.get("email"):
        cfg.email = EmailConfig(**{k: v for k, v in email_raw.items() if k in EmailConfig.__dataclass_fields__})

    return cfg


def write_example() -> None:
    """Write an example feeds.yaml if none exists."""
    path = _feeds_path()
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    example = """\
# Nano Claw feed sources — edit to add/remove sources.
# Changes are picked up automatically before each daily digest.

digest_time: "08:00"  # UTC, 24h format

rss:
  - url: "https://hnrss.org/frontpage"
    name: "Hacker News"
    summarize: true
    max_items: 5

  # - url: "https://feeds.reuters.com/reuters/topNews"
  #   name: "Reuters"
  #   summarize: true
  #   max_items: 3

youtube:
  # - channel_id: "UCxxxxxx"
  #   name: "Channel Name"
  #   max_videos: 2
  #   transcribe: true

substack:
  # - url: "https://author.substack.com/feed"
  #   name: "Author Name"
  #   summarize: true

github:
  # - repo: "owner/repo"
  #   watch: [commits, pulls, releases]

email:
  folders: ["INBOX"]
  max_emails: 20
  summarize: true
"""
    path.write_text(example)
