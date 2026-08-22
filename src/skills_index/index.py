"""Merge scanned GitHub skills (baseline) with skills.sh metadata into index.jsonl."""

from __future__ import annotations

from pathlib import Path

from .config import (
    BY_SOURCE_DIR,
    FETCHED_SKILLS,
    INDEX_JSONL,
    JSON,
    SCANNED_FILE,
    dir_to_source,
    iter_repo_dirs,
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


def _dedup_skills(records: list[Record]) -> tuple[list[Record], int]:
    """Drop cross-repo duplicates: same skillId + same non-empty description.

    skillId 是技能目录名（非全局唯一，同名不同实现的技能真实存在），单独
    相同不足以判定重复；叠加 frontmatter description 完全一致才视为同一
    技能的镜像/拷贝，保留 installs 更高者（fetch 顺序破平局，即 skills.sh
    排名靠前者）。description 为空的记录不参与去重：未知不等于相同。
    Returns ``(kept, dropped_count)``.
    """
    groups: dict[tuple[str, str], list[int]] = {}
    for i, rec in enumerate(records):
        sid = str(rec.get("skillId", ""))
        desc = str(rec.get("description", "") or "").strip()
        if sid and desc:
            groups.setdefault((sid, desc), []).append(i)
    drop: set[int] = set()
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        winner = max(idxs, key=lambda i: (records[i].get("installs") or 0, -i))
        drop.update(i for i in idxs if i != winner)
    if not drop:
        return records, 0
    return [r for i, r in enumerate(records) if i not in drop], len(drop)


def _filled(rec: Record) -> Record:
    """installs / weeklyInstalls 缺失时填 0 / []，保证每条记录形状统一。"""
    rec.setdefault("installs", 0)
    rec.setdefault("weeklyInstalls", [])
    return rec


def run_index(base_dir: Path = BY_SOURCE_DIR) -> tuple[list[Record], dict[str, JSON]]:
    """Merge every repo's scanned skills with skills.sh metadata into index.jsonl.

    - each repo's `scanned.jsonl` is the baseline: every scanned skill is
      written to index.jsonl.
    - `fetched-skills.jsonl` provides the skills.sh metadata (installs /
      weeklyInstalls), joined on `source` + `skillId`. Scanned skills with no
      fetched counterpart ("scan-only", e.g. not registered on skills.sh) keep
      empty metadata (`installs: 0`, `weeklyInstalls: []`).
    - fetched skills with no scanned counterpart (removed from the repo) are
      dropped: only skills a repo scan confirms belong in the index.
    - output order: skills with fetched data keep the skills.sh ranking order;
      scan-only skills are appended at the end.

    Returns ``(index_records, summary)`` where ``summary`` holds counts for the
    run report.
    """
    # Index only merges skills whose repo was scanned in step 2. Step 2 drops
    # repos above the skillCount cap (config.MAX_SKILL_COUNT) and deletes their
    # by-source cache, so those repos never reach this step.
    fetched_list = read_jsonl(FETCHED_SKILLS)
    fetched: dict[tuple[str, str], Record] = {}
    rank: dict[tuple[str, str], int] = {}
    for i, r in enumerate(fetched_list):
        k = _key(r)
        if k not in fetched:
            fetched[k] = r
            rank[k] = i
    if not fetched:
        print(f"[index] no fetched data at {FETCHED_SKILLS}; installs will be empty")
    summary: dict[str, JSON] = {
        "fetched": len(fetched),
        "scanned_merged": 0,
        "scan_only": 0,
        "not_in_repo": 0,
        "deduped_skills": 0,
        "index": 0,
    }

    matched: list[tuple[int, Record]] = []
    scan_only: list[Record] = []
    matched_keys: set[tuple[str, str]] = set()

    subdirs = iter_repo_dirs(base_dir)
    for dir_name in subdirs:
        source = dir_to_source(dir_name)
        gh_path = base_dir / dir_name / SCANNED_FILE
        for rec in read_jsonl(gh_path):
            skill_id = Path(str(rec.get("path", ""))).name
            key = (source, skill_id)
            base = fetched.get(key)
            if base is None:
                # GitHub repo contains a SKILL.md not registered on skills.sh:
                # still indexed, with empty skills.sh metadata.
                scan_only.append({"source": source, "skillId": skill_id, **rec})
                continue
            matched.append((rank[key], {**base, **rec}))
            matched_keys.add(key)

    # Skills with fetched data keep the skills.sh ranking order; scan-only
    # skills are appended afterwards (repo-dir order, path order within repo).
    matched.sort(key=lambda t: t[0])
    result = [_ordered(_filled(rec)) for _, rec in matched]
    result += [_ordered(_filled(rec)) for rec in scan_only]
    # 跨仓库重复（skillId + description 双匹配）只保留 installs 更高者。
    result, deduped = _dedup_skills(result)
    write_jsonl(INDEX_JSONL, result)
    summary["scanned_merged"] = len(matched)
    summary["scan_only"] = len(scan_only)
    summary["not_in_repo"] = len(fetched) - len(matched_keys)
    summary["deduped_skills"] = deduped
    summary["index"] = len(result)
    msg = (
        f"[index] merged {len(matched)} scanned with skills.sh data, "
        f"{len(scan_only)} scan-only (empty installs), "
        f"dropped {summary['not_in_repo']} not-in-repo"
        + (f", deduped {deduped} cross-repo" if deduped else "")
        + f" -> {len(result)} in {INDEX_JSONL}"
    )
    print(msg)
    return result, summary


def _key(rec: Record) -> tuple[str, str]:
    return (str(rec.get("source", "")), str(rec.get("skillId", "")))
