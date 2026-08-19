"""Combine fetched skills.sh data with scanned GitHub skills into index.jsonl."""

from __future__ import annotations

from pathlib import Path

from .config import (
    BY_SOURCE_DIR,
    GITHUB_FILE,
    INDEX_JSONL,
    JSON,
    SKILLS_SH_ALL,
    dir_to_source,
)
from .io_utils import read_jsonl, write_jsonl

Record = dict[str, JSON]


def run_index(base_dir: Path = BY_SOURCE_DIR) -> list[Record]:
    """Merge the fetch output with every repo's scanned skills into index.jsonl.

    - `skills-sh-all.jsonl` provides the skills.sh metadata (name / installs / ...).
    - each `skills-github.jsonl` provides the scanned GitHub URL + path.
    Records are joined on `source` + `skillId`; scanned fields (url, path)
    override / fill in the fetch-only records.
    """
    fetched = {_key(r): r for r in read_jsonl(SKILLS_SH_ALL)}
    if not fetched:
        print(f"[index] no fetched data at {SKILLS_SH_ALL}; run `fetch` first")
        write_jsonl(INDEX_JSONL, [])
        return []

    merged: dict[tuple[str, str], Record] = dict(fetched)

    subdirs = sorted(
        d.name for d in base_dir.iterdir()
        if d.is_dir() and d.name.count("__") == 1
    )
    scanned_count = 0
    for dir_name in subdirs:
        source = dir_to_source(dir_name)
        gh_path = base_dir / dir_name / GITHUB_FILE
        for rec in read_jsonl(gh_path):
            skill_id = Path(str(rec.get("path", ""))).name
            key = (source, skill_id)
            base = merged.get(key, {"source": source, "skillId": skill_id})
            base.update(rec)
            merged[key] = base
            scanned_count += 1

    result = list(merged.values())
    write_jsonl(INDEX_JSONL, result)
    msg = (
        f"[index] merged {len(fetched)} fetched + {scanned_count} scanned "
        f"-> {len(result)} in {INDEX_JSONL}"
    )
    print(msg)
    return result


def _key(rec: Record) -> tuple[str, str]:
    return (str(rec.get("source", "")), str(rec.get("skillId", "")))
