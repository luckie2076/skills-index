"""Fetch skills.sh data and distribute by source."""

from __future__ import annotations

import time
from pathlib import Path

from .config import (
    BY_SOURCE_DIR,
    KEEP_FIELDS,
    SKILLS_API,
    SKILLS_SH_ALL,
    Record,
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
    """Group skills by source into `base_dir/<owner>__<repo>/skills-sh.jsonl`."""
    groups: dict[str, list[Record]] = {}
    for s in skills:
        src = str(s.get("source", "")).strip()
        if src:
            groups.setdefault(src, []).append(s)

    total = 0
    for src, items in groups.items():
        dir_path = base_dir / source_to_dir(src)
        write_jsonl(dir_path / "skills-sh.jsonl", items)
        total += len(items)
        print(f"  wrote {dir_path / 'skills-sh.jsonl'}: {len(items)}")
    return len(groups), total


def run_fetch(*, max_pages: int = 0, token: str = "") -> list[Record]:
    """Fetch skills.sh data, filter GitHub sources, and save.

    Saves only the raw skills.sh fields (source / skillId / name / installs /
    weeklyInstalls / url). GitHub URLs are discovered later by `scan`, which
    walks each repo's SKILL.md files — so no URL resolution happens here.
    """
    raw, failed_pages = fetch_all(max_pages, token=token)
    if failed_pages:
        print(f"fetch done with {len(failed_pages)} skipped page(s): {failed_pages}")
    skills, dropped = filter_github(raw)
    print(f"filtered non-GitHub sources: dropped {dropped}, kept {len(skills)}")

    write_jsonl(SKILLS_SH_ALL, skills)
    print(f"saved {len(skills)} skills to {SKILLS_SH_ALL}")

    dirs, total = distribute_by_source(skills)
    print(f"distributed into {dirs} source dirs, {total} records")
    return skills
