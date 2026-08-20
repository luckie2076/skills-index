"""GitHub API surface: branches, skill directories, metadata, and SKILL.md content.

Only metadata and tree/contents/blob endpoints are used -- repositories are
never cloned or downloaded.
"""

from __future__ import annotations

import base64
from functools import cache

import httpx
import yaml

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
    return {name: path for name, (path, _sha) in _parse_skill_blobs(tree_items).items()}


def _parse_skill_blobs(tree_items: list[Record]) -> dict[str, tuple[str, str]]:
    """From a git tree, return {basename: (relative_path, blob_sha)} for SKILL.md blobs."""
    blobs: dict[str, tuple[str, str]] = {}
    for item in tree_items:
        if item.get("type") == "blob" and item.get("path", "").endswith("/SKILL.md"):
            rel = item["path"][: -len("/SKILL.md")]
            blobs[rel.rsplit("/", 1)[-1]] = (rel, str(item.get("sha", "")))
    return blobs


def _walk_contents(  # noqa: E501
    client: httpx.Client, owner: str, repo: str, branch: str, path: str
) -> dict[str, tuple[str, str]]:
    """Recursively walk the Contents API (fallback for truncated trees)."""
    out: dict[str, tuple[str, str]] = {}
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
            out[rel.rsplit("/", 1)[-1]] = (rel, str(it.get("sha", "")))
    return out


@cache
def _skill_blobs(  # noqa: E501
    source: str, branch: str, *, client: httpx.Client | None = None
) -> dict[str, tuple[str, str]]:
    """Return {basename: (relative_path, blob_sha)} for every SKILL.md (cached)."""
    owner, repo = _split(source)
    client = client or new_github_client()
    data = get_json(client, f"/repos/{owner}/{repo}/git/trees/{branch}?recursive=1")
    blobs = _parse_skill_blobs(data.get("tree", []))
    if data.get("truncated"):
        blobs.update(_walk_contents(client, owner, repo, branch, "skills"))
    return blobs


@cache
def get_skill_blobs(  # noqa: E501
    source: str, branch: str, *, client: httpx.Client | None = None
) -> dict[str, tuple[str, str]]:
    """Return {basename: (relative_path, blob_sha)} for every SKILL.md (cached).

    The blob sha is a content-addressed fingerprint of each SKILL.md file, used
    for file-level incremental rescans.
    """
    return _skill_blobs(source, branch, client=client)


@cache
def get_skill_dirs(  # noqa: E501
    source: str, branch: str, *, client: httpx.Client | None = None
) -> dict[str, str]:
    """Return {basename: relative_path} of every dir containing SKILL.md."""
    blobs = get_skill_blobs(source, branch, client=client)
    return {name: path for name, (path, _sha) in blobs.items()}


def _fetch_blob(client: httpx.Client, owner: str, repo: str, sha: str) -> str:
    """Fetch a git blob and decode it as UTF-8 text."""
    data = get_json(client, f"/repos/{owner}/{repo}/git/blobs/{sha}")
    if data.get("encoding") != "base64":
        return ""
    raw = base64.b64decode(data.get("content", ""))
    return raw.decode("utf-8", errors="replace")


def extract_description(markdown: str) -> str:
    """Return the `description` from a SKILL.md YAML frontmatter (empty if absent)."""
    text = markdown.lstrip("\ufeff").lstrip()
    if not text.startswith("---"):
        return ""
    body = text[3:].lstrip("\n")
    parts = body.split("\n---", 1)
    if len(parts) < 2:
        return ""
    try:
        data = yaml.safe_load(parts[0]) or {}
    except yaml.YAMLError:
        return ""
    desc = data.get("description")
    return str(desc).strip() if desc else ""


# Per-run content cache keyed by blob sha (shas are content-addressed, so a
# sha uniquely identifies a file, even across repos).
_blob_content: dict[str, str] = {}


def get_skill_descriptions(  # noqa: E501
    source: str,
    blobs: dict[str, tuple[str, str]],
    *,
    client: httpx.Client | None = None,
) -> dict[str, str]:
    """Return {basename: description}, fetching only the given SKILL.md blobs.

    `blobs` is a {basename: (relative_path, blob_sha)} subset -- typically just
    the blobs whose sha changed since the last scan -- so unchanged skills are
    never re-downloaded.
    """
    if not blobs:
        return {}
    owner, repo = _split(source)
    client = client or new_github_client()
    out: dict[str, str] = {}
    for name, (_path, sha) in blobs.items():
        if not sha:
            out[name] = ""
            continue
        content = _blob_content.get(sha)
        if content is None:
            try:
                content = _fetch_blob(client, owner, repo, sha)
            except Exception as exc:
                print(f"  [skip] {name}: blob fetch failed - {exc}")
                out[name] = ""
                continue
            _blob_content[sha] = content
        out[name] = extract_description(content)
    return out


def get_repo_meta(source: str, *, client: httpx.Client | None = None) -> tuple[str, str]:
    """Return (pushed_at, default_branch)."""
    return _repo_info(source, client=client)
