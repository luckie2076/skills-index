"""Scan GitHub repositories for skills (incremental via pushed_at)."""

from __future__ import annotations

import datetime
import time
from pathlib import Path

from .config import (
    BY_SOURCE_DIR,
    JSON,
    META_FILE,
    SCANNED_FILE,
    SCANNED_REPOS,
    SCHEMA_VERSION,
    dir_to_source,
)

# META_FILE holds GitHub-sourced metadata (branch / pushedAt / skillCount).
from .github import (
    get_repo_meta,
    get_repo_metas,
    get_skill_blobs,
    get_skill_descriptions,
)
from .http import new_github_client
from .io_utils import read_json, read_jsonl, write_json, write_jsonl


def plan_blob_fetches(
    blobs: dict[str, tuple[str, str]],
    prev_shas: dict[str, str],
    *,
    force: bool,
    schema_upgrade: bool,
) -> dict[str, tuple[str, str]]:
    """Return the blobs whose SKILL.md description must be (re)fetched.

    A schema upgrade or `--force` refetches everything; otherwise only blobs
    whose sha differs from the previous scan are fetched (file-level
    incremental), so untouched skills are never re-downloaded.
    """
    if force or schema_upgrade:
        return dict(blobs)
    return {
        name: (path, sha)
        for name, (path, sha) in blobs.items()
        if prev_shas.get(path) != sha
    }


def merge_skill_records(  # noqa: E501
    blobs: dict[str, tuple[str, str]],
    descriptions: dict[str, str],
    old_records: list[JSON],
    prev_shas: dict[str, str],
    *,
    force: bool,
    schema_upgrade: bool,
) -> list[JSON]:
    """Rebuild a repo's scanned.jsonl records from the current git tree.

    Untouched skills keep their old record; changed/new ones are rebuilt from
    the freshly fetched `descriptions`; paths that vanished from the tree are
    dropped. Records are sorted by path for stable output.
    """
    old = {str(rec.get("path", "")): rec for rec in old_records}
    out: list[JSON] = []
    for name, (path, sha) in blobs.items():
        if (
            not force
            and not schema_upgrade
            and old.get(path)
            and prev_shas.get(path) == sha
        ):
            out.append(old[path])
        else:
            out.append({"path": path, "description": descriptions.get(name, "")})
    out.sort(key=lambda rec: str(rec.get("path", "")))
    return out


def scan_repositories(*, force: bool = False, base_dir: Path = BY_SOURCE_DIR) -> dict:
    """Walk `base_dir`, skip unchanged repos by `pushed_at`, emit per-repo files.

    Returns a summary dict with counts for the run report.
    """
    client = new_github_client()
    now = datetime.datetime.now(datetime.UTC).isoformat()
    _t0 = time.monotonic()
    _meta_time = 0.0
    _blob_time = 0.0

    subdirs = sorted(
        d.name for d in base_dir.iterdir()
        if d.is_dir() and d.name.count("__") == 1
    )
    print(f"scanning by-source: {len(subdirs)} GitHub repo dirs" + (" (force)" if force else ""))

    # Fetch all repo metadata concurrently (network-bound) before the loop.
    _tm = time.monotonic()
    sources = [dir_to_source(d) for d in subdirs]
    metas = get_repo_metas(sources, client=client)
    _meta_time += time.monotonic() - _tm

    skipped = 0
    updated = 0
    failed = 0
    total_skills = 0
    repos: list[JSON] = []
    for dir_name in subdirs:
        source = dir_to_source(dir_name)
        repo_dir = base_dir / dir_name
        meta_path = repo_dir / META_FILE

        if source not in metas:
            failed += 1
            continue
        pushed, branch = metas[source]

        prev = read_json(meta_path, default={}) or {}
        schema_upgrade = prev.get("schemaVersion") != SCHEMA_VERSION
        up_to_date = (
            not force
            and meta_path.exists()
            and prev.get("pushedAt") == pushed
            and not schema_upgrade
        )
        if up_to_date:
            skipped += 1
            print(f"  [skip] {source}: pushed_at unchanged ({pushed})")
            # 已扫描过的仓库仍纳入汇总，从既有产物读取
            repos.append(_summarize_repo(repo_dir, meta_path, source))
            continue

        try:
            _tb = time.monotonic()
            blobs = get_skill_blobs(source, branch, client=client)
        except Exception as exc:
            print(f"  [skip] {source}: scan failed - {exc}")
            failed += 1
            continue

        # File-level incremental: only fetch blobs whose sha changed.
        prev_shas = dict(prev.get("blobShas") or {})
        fetch = plan_blob_fetches(
            blobs, prev_shas, force=force, schema_upgrade=schema_upgrade
        )
        try:
            _tb = time.monotonic()
            descriptions = get_skill_descriptions(source, fetch, client=client)
            _blob_time += time.monotonic() - _tb
        except Exception as exc:
            print(f"  [skip] {source}: description fetch failed - {exc}")
            failed += 1
            continue

        skills = merge_skill_records(
            blobs,
            descriptions,
            read_jsonl(repo_dir / SCANNED_FILE),
            prev_shas,
            force=force,
            schema_upgrade=schema_upgrade,
        )
        write_jsonl(repo_dir / SCANNED_FILE, skills)

        meta = {
            "source": source,
            "branch": branch,
            "pushedAt": pushed,
            "lastScanned": now,
            "skillCount": len(skills),
            "truncated": False,
            "schemaVersion": SCHEMA_VERSION,
            "blobShas": {path: sha for _name, (path, sha) in blobs.items()},
        }
        write_json(meta_path, meta)

        repos.append(_summarize_repo(repo_dir, meta_path, source))
        updated += 1
        total_skills += len(skills)
        print(
            f"  [scan] {source}: {len(skills)} skills "
            f"({len(fetch)}/{len(blobs)} blobs fetched)"
        )

    write_jsonl(SCANNED_REPOS, repos)
    print(f"scan done: skipped {skipped} unchanged, updated {updated}, failed {failed}.")
    print(f"wrote {SCANNED_REPOS.name}: {len(repos)} repos")
    _total = time.monotonic() - _t0
    print(
        f"[timer] scan: total={_total:.1f}s "
        f"meta={_meta_time:.1f}s blob+desc={_blob_time:.1f}s "
        f"other={_total - _meta_time - _blob_time:.1f}s"
    )
    summary = {
        "repos_total": len(subdirs),
        "repos_skipped": skipped,
        "repos_updated": updated,
        "repos_failed": failed,
        "skills_scanned": total_skills,
    }
    return summary


def _summarize_repo(repo_dir: Path, meta_path: Path, source: str) -> JSON:
    """Read a repo's persisted meta + skills into a single summary record."""
    from .io_utils import read_jsonl

    meta = read_json(meta_path, default={}) or {}
    skills = read_jsonl(repo_dir / SCANNED_FILE)
    return {
        "source": source,
        "branch": meta.get("branch"),
        "pushedAt": meta.get("pushedAt"),
        "lastScanned": meta.get("lastScanned"),
        "skillCount": meta.get("skillCount", len(skills)),
        "skills": [s["path"] for s in skills],
    }
