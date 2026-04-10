"""
GitHub repo poller — surfaces new commits, pull requests, and releases.

Uses the GitHub REST API. Set GITHUB_TOKEN in .env for private repos or
higher rate limits (5000 req/h vs 60 req/h unauthenticated).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

from ..config import settings
from . import state
from .config import GitHubSource

logger = logging.getLogger(__name__)

_API = "https://api.github.com"


@dataclass
class GitHubUpdate:
    repo: str
    kind: str  # "commit" | "pull" | "release"
    title: str
    url: str
    author: str = ""
    body: str = ""


async def fetch_repo(source: GitHubSource) -> list[GitHubUpdate]:
    updates: list[GitHubUpdate] = []
    headers = {"Accept": "application/vnd.github+json"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"

    async with httpx.AsyncClient(headers=headers, timeout=15) as client:
        if "commits" in source.watch:
            updates.extend(await _fetch_commits(client, source.repo))
        if "pulls" in source.watch:
            updates.extend(await _fetch_pulls(client, source.repo))
        if "releases" in source.watch:
            updates.extend(await _fetch_releases(client, source.repo))

    return updates


async def _fetch_commits(client: httpx.AsyncClient, repo: str) -> list[GitHubUpdate]:
    last_sha = await state.get("github", repo, "last_commit_sha")
    try:
        resp = await client.get(f"{_API}/repos/{repo}/commits", params={"per_page": 10})
        resp.raise_for_status()
        commits = resp.json()
    except Exception as e:
        logger.warning(f"GitHub commits fetch failed for {repo}: {e}")
        return []

    if not commits:
        return []

    items = []
    for c in commits:
        sha = c.get("sha", "")
        if sha == last_sha:
            break
        msg = c.get("commit", {}).get("message", "").split("\n")[0]
        author = c.get("commit", {}).get("author", {}).get("name", "")
        html_url = c.get("html_url", f"https://github.com/{repo}/commit/{sha}")
        items.append(GitHubUpdate(
            repo=repo, kind="commit",
            title=f"{msg[:80]} ({sha[:7]})",
            url=html_url, author=author,
        ))

    if commits:
        new_sha = commits[0].get("sha", "")
        if new_sha:
            await state.set_value("github", repo, "last_commit_sha", new_sha)

    return items[:5]  # cap at 5 commits per digest


async def _fetch_pulls(client: httpx.AsyncClient, repo: str) -> list[GitHubUpdate]:
    last_pr = await state.get("github", repo, "last_pr_number")
    last_pr_num = int(last_pr) if last_pr else 0
    try:
        resp = await client.get(f"{_API}/repos/{repo}/pulls",
                                params={"state": "open", "sort": "created", "direction": "desc", "per_page": 10})
        resp.raise_for_status()
        pulls = resp.json()
    except Exception as e:
        logger.warning(f"GitHub pulls fetch failed for {repo}: {e}")
        return []

    new_pulls = [p for p in pulls if p.get("number", 0) > last_pr_num]
    if new_pulls:
        await state.set_value("github", repo, "last_pr_number", str(new_pulls[0]["number"]))

    return [
        GitHubUpdate(
            repo=repo, kind="pull",
            title=f"PR #{p['number']}: {p.get('title', '')}",
            url=p.get("html_url", ""),
            author=p.get("user", {}).get("login", ""),
            body=(p.get("body") or "")[:300],
        )
        for p in new_pulls[:5]
    ]


async def _fetch_releases(client: httpx.AsyncClient, repo: str) -> list[GitHubUpdate]:
    last_tag = await state.get("github", repo, "last_release_tag")
    try:
        resp = await client.get(f"{_API}/repos/{repo}/releases", params={"per_page": 5})
        resp.raise_for_status()
        releases = resp.json()
    except Exception as e:
        logger.warning(f"GitHub releases fetch failed for {repo}: {e}")
        return []

    new_releases = []
    for r in releases:
        tag = r.get("tag_name", "")
        if tag == last_tag:
            break
        new_releases.append(r)

    if new_releases and releases:
        await state.set_value("github", repo, "last_release_tag", releases[0].get("tag_name", ""))

    return [
        GitHubUpdate(
            repo=repo, kind="release",
            title=f"{r.get('tag_name', '')} — {r.get('name', '')}",
            url=r.get("html_url", ""),
            body=(r.get("body") or "")[:400],
        )
        for r in new_releases[:3]
    ]
