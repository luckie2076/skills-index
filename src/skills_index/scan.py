"""Scan GitHub repositories for skills (incremental via pushed_at)."""

from __future__ import annotations

import datetime
from pathlib import Path

from .config import (
    BY_SOURCE_DIR,
    GITHUB_FILE,
    JSON,
    REPO_META_FILE,
    SCAN_ALL_JSONL,
    dir_to_source,
)

# REPO_META_FILE holds GitHub-sourced metadata (branch / pushedAt / skillCount).
from .github import get_repo_meta, get_skill_dirs
from .http import new_github_client
from .io_utils import read_json, write_json, write_jsonl


def scan_repositories(*, force: bool = False, base_dir: Path = BY_SOURCE_DIR) -> None:
    """Walk `base_dir`, skip unchanged repos by `pushed_at`, emit per-repo files."""
    client = new_github_client()
    now = datetime.datetime.now(datetime.UTC).isoformat()

    subdirs = sorted(
        d.name for d in base_dir.iterdir()
        if d.is_dir() and d.name.count("__") == 1
    )
    print(f"scanning by-source: {len(subdirs)} GitHub repo dirs" + (" (force)" if force else ""))

    skipped = 0
    repos: list[JSON] = []
    for dir_name in subdirs:
        source = dir_to_source(dir_name)
        repo_dir = base_dir / dir_name
        meta_path = repo_dir / REPO_META_FILE

        try:
            pushed, branch = get_repo_meta(source, client=client)
        except Exception as exc:
            print(f"  [skip] {source}: meta fetch failed - {exc}")
            continue

        prev = read_json(meta_path, default={}) or {}
        if (not force) and meta_path.exists() and prev.get("pushedAt") == pushed:
            skipped += 1
            print(f"  [skip] {source}: pushed_at unchanged ({pushed})")
            # 已扫描过的仓库仍纳入汇总，从既有产物读取
            repos.append(_summarize_repo(repo_dir, meta_path, source))
            continue

        try:
            dirs = get_skill_dirs(source, branch, client=client)
        except Exception as exc:
            print(f"  [skip] {source}: scan failed - {exc}")
            continue

        skills = [{"path": path} for path in dirs.values()]
        write_jsonl(repo_dir / GITHUB_FILE, skills)

        meta = {
            "source": source,
            "branch": branch,
            "pushedAt": pushed,
            "lastScanned": now,
            "skillCount": len(skills),
            "truncated": False,
        }
        write_json(meta_path, meta)

        repos.append(_summarize_repo(repo_dir, meta_path, source))
        print(f"  [scan] {source}: {len(skills)} skills")

    write_jsonl(SCAN_ALL_JSONL, repos)
    print(f"scan done: skipped {skipped} unchanged, processed {len(subdirs) - skipped}.")
    print(f"wrote {SCAN_ALL_JSONL.name}: {len(repos)} repos")


def _summarize_repo(repo_dir: Path, meta_path: Path, source: str) -> JSON:
    """Read a repo's persisted meta + skills into a single summary record."""
    from .io_utils import read_jsonl

    meta = read_json(meta_path, default={}) or {}
    skills = read_jsonl(repo_dir / GITHUB_FILE)
    return {
        "source": source,
        "branch": meta.get("branch"),
        "pushedAt": meta.get("pushedAt"),
        "lastScanned": meta.get("lastScanned"),
        "skillCount": meta.get("skillCount", len(skills)),
        "skills": [s["path"] for s in skills],
    }
