"""GitHub API surface: branches, skill directories, and metadata.

Only metadata and tree/contents endpoints are used -- repository file
contents are never cloned or downloaded.
"""

from __future__ import annotations

from functools import cache

import httpx

from .config import Record
from .http import get_json, new_github_client


# Process-wide cache (valid for a single run only).
@cache
def _repo_info(source: str, *, client: httpx.Client | None = None) -> tuple[str, str]:
    """Return (pushed_at, default_branch) for `source`, cached for the run."""
    owner, repo = _split(source)
    client = client or new_github_client()
    data = get_json(client, f"/repos/{owner}/{repo}")
    pushed = data.get("pushed_at") or data.get("updated_at") or ""
    branch = str(data.get("default_branch", "main"))
    return pushed, branch


def _split(source: str) -> tuple[str, str]:
    owner, repo = source.split("/", 1)
    return owner, repo


def get_default_branch(source: str, *, client: httpx.Client | None = None) -> str:
    """Return the repository default branch (cached)."""
    return _repo_info(source, client=client)[1]


def _parse_skill_dirs(tree_items: list[Record]) -> dict[str, str]:
    """From a git tree, return {basename: relative_path} for dirs with SKILL.md."""
    dirs: dict[str, str] = {}
    for item in tree_items:
        if item.get("type") == "blob" and item.get("path", "").endswith("/SKILL.md"):
            rel = item["path"][: -len("/SKILL.md")]
            dirs[rel.rsplit("/", 1)[-1]] = rel
    return dirs


def _walk_contents(  # noqa: E501
    client: httpx.Client, owner: str, repo: str, branch: str, path: str
) -> dict[str, str]:
    """Recursively walk the Contents API (fallback for truncated trees)."""
    out: dict[str, str] = {}
    try:
        url = f"/repos/{owner}/{repo}/contents/{path}?ref={branch}"
        items: list[Record] = get_json(client, url)
    except Exception:
        return out
    for it in items:
        if it.get("type") == "dir":
            out.update(_walk_contents(client, owner, repo, branch, it["path"]))
        elif it.get("type") == "file" and it.get("name") == "SKILL.md":
            rel = it["path"][: -len("/SKILL.md")]
            out[rel.rsplit("/", 1)[-1]] = rel
    return out


@cache
def get_skill_dirs(  # noqa: E501
    source: str, branch: str, *, client: httpx.Client | None = None
) -> dict[str, str]:
    """Return {basename: relative_path} of every dir containing SKILL.md."""
    owner, repo = _split(source)
    client = client or new_github_client()
    data = get_json(client, f"/repos/{owner}/{repo}/git/trees/{branch}?recursive=1")
    dirs = _parse_skill_dirs(data.get("tree", []))
    if data.get("truncated"):
        dirs.update(_walk_contents(client, owner, repo, branch, "skills"))
    return dirs


def get_repo_meta(source: str, *, client: httpx.Client | None = None) -> tuple[str, str]:
    """Return (pushed_at, default_branch)."""
    return _repo_info(source, client=client)
