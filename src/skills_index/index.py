"""Combine fetched skills.sh data with scanned GitHub skills into index.jsonl."""

from __future__ import annotations

from pathlib import Path

from .config import (
    BY_SOURCE_DIR,
    FETCHED_SKILLS,
    INDEX_JSONL,
    JSON,
    SCANNED_FILE,
    dir_to_source,
)
from .io_utils import read_jsonl, write_jsonl

Record = dict[str, JSON]

# Column order for the emitted index.jsonl records.
_INDEX_FIELD_ORDER = (
    "skillId",
    "source",
    "description",
    "installs",
    "weeklyInstalls",
    "path",
)


def _ordered(rec: Record) -> Record:
    """Return `rec` with keys ordered for index.jsonl output.

    Known fields come first in a stable order; any remaining keys are appended
    in their original (insertion) order.
    """
    out: Record = {}
    for k in _INDEX_FIELD_ORDER:
        if k in rec:
            out[k] = rec[k]
    for k, v in rec.items():
        if k not in out:
            out[k] = v
    return out


def run_index(base_dir: Path = BY_SOURCE_DIR) -> tuple[list[Record], dict[str, JSON]]:
    """Merge the fetch output with every repo's scanned skills into index.jsonl.

    - `fetched-skills.jsonl` provides the skills.sh metadata (name / installs / ...).
    - each `scanned.jsonl` provides the scanned GitHub `path` + `description`.
    Records are joined on `source` + `skillId`; scanned fields (path, description)
    fill in the fetch-only records. Only skills actually present in a repo scan
    are written to index.jsonl: fetched skills with no scanned counterpart (e.g.
    removed from the repo) are dropped.

    Returns ``(index_records, summary)`` where ``summary`` holds counts for the
    run report.
    """
    # Index only merges skills whose repo was scanned in step 2. Step 2 already
    # drops repos below the star threshold (config.MIN_STARS) and deletes their
    # by-source cache, so low-star repos never reach this step. No extra star
    # filter is needed here.
    fetched = {_key(r): r for r in read_jsonl(FETCHED_SKILLS)}
    summary: dict[str, JSON] = {
        "fetched": 0,
        "scanned_merged": 0,
        "orphans": 0,
        "not_in_repo": 0,
        "index": 0,
    }
    if not fetched:
        print(f"[index] no fetched data at {FETCHED_SKILLS}; run `fetch` first")
        write_jsonl(INDEX_JSONL, [])
        return [], summary

    summary["fetched"] = len(fetched)
    merged: dict[tuple[str, str], Record] = dict(fetched)
    matched_keys: set[tuple[str, str]] = set()

    subdirs = sorted(
        d.name for d in base_dir.iterdir()
        if d.is_dir() and d.name.count("__") == 1
    )
    scanned_count = 0
    orphan_count = 0
    for dir_name in subdirs:
        source = dir_to_source(dir_name)
        gh_path = base_dir / dir_name / SCANNED_FILE
        for rec in read_jsonl(gh_path):
            skill_id = Path(str(rec.get("path", ""))).name
            key = (source, skill_id)
            base = merged.get(key)
            if base is None:
                # GitHub repo contains a SKILL.md not registered on skills.sh.
                # The index is scoped to the skills.sh ranking, so these
                # "orphan" skills are intentionally excluded from index.jsonl
                # (they remain available in data/by-source for other uses).
                orphan_count += 1
                continue
            base.update(rec)
            merged[key] = base
            matched_keys.add(key)
            scanned_count += 1

    # Only skills confirmed by a repo scan belong in the index; fetched skills
    # missing from the scan are dropped (keeps fetched order).
    not_in_repo = len(fetched) - len(matched_keys)
    result = [_ordered(merged[k]) for k in fetched if k in matched_keys]
    write_jsonl(INDEX_JSONL, result)
    summary["scanned_merged"] = scanned_count
    summary["orphans"] = orphan_count
    summary["not_in_repo"] = not_in_repo
    summary["index"] = len(result)
    msg = (
        f"[index] merged {scanned_count} scanned into {len(fetched)} fetched "
        f"(skipped {orphan_count} orphan, dropped {not_in_repo} not-in-repo) "
        f"-> {len(result)} in {INDEX_JSONL}"
    )
    print(msg)
    return result, summary


def _key(rec: Record) -> tuple[str, str]:
    return (str(rec.get("source", "")), str(rec.get("skillId", "")))
