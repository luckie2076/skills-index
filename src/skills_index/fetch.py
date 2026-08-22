"""Fetch skills.sh data and distribute by source."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from .config import (
    BY_SOURCE_DIR,
    DIR_SEP,
    FETCHED_FILE,
    FETCHED_SKILLS,
    JSON,
    KEEP_FIELDS,
    SKILLS_API,
    Record,
    dir_to_source,
    is_github_source,
    source_to_dir,
)
from .http import POLITE_PAUSE, HttpError, build_client, get_json
from .io_utils import write_jsonl

# Give up after this many consecutive page failures (each page already
# retried internally by http.get_json). Prevents an infinite loop against
# a dead API while still tolerating isolated page errors.
MAX_CONSECUTIVE_PAGE_FAILURES = 3


def fetch_all(max_pages: int = 0, *, token: str = "") -> tuple[list[Record], list[int]]:
    """Fetch every page of skills.sh `all-time` rankings.

    `max_pages=0` (default) fetches until `hasMore` is false. A page that
    fails after its internal retries is skipped and recorded; fetching stops
    only after `MAX_CONSECUTIVE_PAGE_FAILURES` consecutive failures.

    Returns ``(skills, failed_page_numbers)``.
    """
    client = build_client(token)
    out: list[Record] = []
    failed: list[int] = []
    consecutive_failures = 0
    page = 0
    while max_pages == 0 or page < max_pages:
        try:
            data = get_json(client, f"{SKILLS_API}/{page}")
        except HttpError as exc:
            consecutive_failures += 1
            failed.append(page)
            print(f"  [skip] page {page}: {exc}")
            if consecutive_failures >= MAX_CONSECUTIVE_PAGE_FAILURES:
                print(
                    f"  [abort] {MAX_CONSECUTIVE_PAGE_FAILURES} consecutive page "
                    "failures; stopping"
                )
                break
            page += 1
            time.sleep(POLITE_PAUSE)
            continue
        consecutive_failures = 0
        batch = data.get("skills", [])
        out.extend(batch)
        print(
            f"page {page}: +{len(batch)} skills, "
            f"hasMore={data.get('hasMore')}, total={data.get('total')}"
        )
        if not data.get("hasMore"):
            break
        page += 1
        time.sleep(POLITE_PAUSE)
    return out, failed


def filter_github(skills: list[Record]) -> tuple[list[Record], int]:
    """Keep only GitHub-sourced skills, whitelisting fields. Returns (kept, dropped)."""
    kept: list[Record] = []
    dropped = 0
    for s in skills:
        if is_github_source(str(s.get("source", ""))):
            kept.append({k: s[k] for k in KEEP_FIELDS if k in s})
        else:
            dropped += 1
    return kept, dropped


def distribute_by_source(skills: list[Record], base_dir: Path = BY_SOURCE_DIR) -> tuple[int, int]:
    """Group skills by source into `base_dir/<owner>__<repo>/fetched.jsonl`."""
    groups: dict[str, list[Record]] = {}
    for s in skills:
        src = str(s.get("source", "")).strip()
        if src:
            groups.setdefault(src, []).append(s)

    total = 0
    for src, items in groups.items():
        dir_path = base_dir / source_to_dir(src)
        write_jsonl(dir_path / FETCHED_FILE, items)
        total += len(items)
        print(f"  wrote {dir_path / FETCHED_FILE}: {len(items)}")
    return len(groups), total


def prune_stale_repos(sources: set[str], base_dir: Path = BY_SOURCE_DIR) -> int:
    """Remove by-source dirs whose source is not in `sources` (incremental mode).

    Called after a full fetch in incremental mode: repos that vanished from the
    skills.sh ranking keep a stale `scanned.jsonl` that would otherwise leak
    into `index.jsonl`. Only dirs matching the `owner__repo` layout (exactly one
    `DIR_SEP`) are considered, so unrelated files stay intact. Returns the
    number of removed dirs.
    """
    removed = 0
    for d in sorted(base_dir.iterdir(), key=lambda p: p.name):
        if not (d.is_dir() and d.name.count(DIR_SEP) == 1):
            continue
        if dir_to_source(d.name) not in sources:
            shutil.rmtree(d)
            removed += 1
            print(f"  [prune] removed stale {d.name}")
    return removed


def run_fetch(*, max_pages: int = 0, token: str = "") -> tuple[list[Record], dict[str, JSON]]:
    """Fetch skills.sh data, filter GitHub sources, and save.

    Saves only the raw skills.sh fields (source / skillId / installs /
    weeklyInstalls). GitHub URLs are discovered later by `scan`, which
    walks each repo's SKILL.md files — so no URL resolution happens here.

    Returns ``(skills, summary)`` where ``summary`` holds counts for the run
    report.
    """
    raw, failed_pages = fetch_all(max_pages, token=token)
    if failed_pages:
        print(f"fetch done with {len(failed_pages)} skipped page(s): {failed_pages}")
    skills, dropped = filter_github(raw)
    print(f"filtered non-GitHub sources: dropped {dropped}, kept {len(skills)}")

    write_jsonl(FETCHED_SKILLS, skills)
    print(f"saved {len(skills)} skills to {FETCHED_SKILLS}")

    dirs, total = distribute_by_source(skills)
    print(f"distributed into {dirs} source dirs, {total} records")
    summary = {
        "raw_skills": len(raw),
        "kept_github": len(skills),
        "dropped_non_github": dropped,
        "source_dirs": dirs,
        "failed_pages": failed_pages,
    }
    return skills, summary
