"""Scan GitHub repositories for skills (incremental via pushed_at)."""

from __future__ import annotations

import datetime
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from .config import (
    BY_SOURCE_DIR,
    FETCHED_SKILLS,
    JSON,
    MAX_SKILL_COUNT,
    META_FILE,
    SCANNED_FILE,
    SCANNED_REPOS,
    SCANNED_REPOS_BY_SKILLCOUNT,
    SCANNED_REPOS_BY_STARS,
    SCHEMA_VERSION,
    dir_to_source,
)

# META_FILE holds GitHub-sourced metadata (branch / pushedAt / stars / skillCount).
from .github import (
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


# Concurrency for repo-level scanning. GitHub requests are I/O-bound, so a
# thread pool overlaps network waits across repos. Capped at 8 to stay friendly
# to GitHub's secondary per-token concurrency limits; the rate-limit-aware
# backoff in http.py absorbs any 429/403 spikes this may trigger.
SCAN_WORKERS = 8


def _scan_one_repo(
    dir_name: str,
    *,
    force: bool,
    now: str,
    base_dir: Path,
    metas: dict[str, tuple[str, str, int]],
    missing: set[str],
    client: httpx.Client,
    counters: dict[str, int],
    max_skill_count: int | None = None,
) -> JSON | None:
    """Scan a single repo dir. Returns its summary record, or None to skip.

    Pure function of its arguments plus the on-disk cache. Shared mutable
    state is limited to `counters` (plain ints, GIL-guarded) and the
    rate-limited http client (thread-safe).
    """
    effective_max = MAX_SKILL_COUNT if max_skill_count is None else max_skill_count
    source = dir_to_source(dir_name)
    repo_dir = base_dir / dir_name
    meta_path = repo_dir / META_FILE

    if source not in metas:
        if source in missing:
            # Repo is definitively gone (404): drop its stale scan data so it
            # (and its skills) no longer appear in the index.
            counters["gone"] += 1
            print(f"  [gone] {source}: repo not found (404); removing stale data")
            if repo_dir.exists():
                shutil.rmtree(repo_dir)
        else:
            counters["failed"] += 1
        return None
    pushed, branch, stars = metas[source]

    prev = read_json(meta_path, default={}) or {}
    schema_upgrade = prev.get("schemaVersion") != SCHEMA_VERSION
    up_to_date = (
        not force
        and meta_path.exists()
        and prev.get("pushedAt") == pushed
        and not schema_upgrade
    )
    if up_to_date:
        # 已扫描过的仓库：仍受 skillCount 上限约束，避免聚合型仓库绕过上限。
        cached = read_json(meta_path, default={}) or {}
        cached_skill_count = cached.get("skillCount") or 0
        if effective_max > 0 and cached_skill_count > effective_max:
            counters["filtered"] += 1
            counters["filtered_high_skill"] += 1
            print(f"  [high-skill] {source}: {cached_skill_count} > {effective_max}")
            if repo_dir.exists():
                shutil.rmtree(repo_dir)
            return None
        counters["skipped"] += 1
        print(f"  [skip] {source}: pushed_at unchanged ({pushed})")
        # 已扫描过的仓库仍纳入汇总，从既有产物读取
        return _summarize_repo(repo_dir, meta_path, source)

    try:
        blobs = get_skill_blobs(source, branch, client=client)
    except Exception as exc:
        print(f"  [skip] {source}: scan failed - {exc}")
        counters["failed"] += 1
        return None

    # File-level incremental: only fetch blobs whose sha changed.
    prev_shas = dict(prev.get("blobShas") or {})
    fetch = plan_blob_fetches(
        blobs, prev_shas, force=force, schema_upgrade=schema_upgrade
    )
    try:
        descriptions = get_skill_descriptions(source, fetch, client=client)
    except Exception as exc:
        print(f"  [skip] {source}: description fetch failed - {exc}")
        counters["failed"] += 1
        return None

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
        "stars": stars,
        "lastScanned": now,
        "skillCount": len(skills),
        "truncated": False,
        "schemaVersion": SCHEMA_VERSION,
        "blobShas": {path: sha for _name, (path, sha) in blobs.items()},
    }
    write_json(meta_path, meta)

    # Drop repos whose skill count exceeds the cap (e.g. aggregator/awesome-list
    # repos) so they never enter the index; remove their stale scan data.
    if effective_max > 0 and len(skills) > effective_max:
        counters["filtered"] += 1
        counters["filtered_high_skill"] += 1
        print(f"  [high-skill] {source}: {len(skills)} > {effective_max}; removing cache")
        if repo_dir.exists():
            shutil.rmtree(repo_dir)
        return None

    counters["updated"] += 1
    counters["total_skills"] += len(skills)
    print(
        f"  [scan] {source}: {len(skills)} skills "
        f"({len(fetch)}/{len(blobs)} blobs fetched)"
    )
    return _summarize_repo(repo_dir, meta_path, source)


def scan_repositories(
    *,
    force: bool = False,
    max_skill_count: int | None = None,
    base_dir: Path = BY_SOURCE_DIR,
) -> dict[str, JSON]:
    """Walk `base_dir`, skip unchanged repos by `pushed_at`, emit per-repo files.

    `max_skill_count` filters out repos with more than that many skills. When
    it is `None` (the default), `config.MAX_SKILL_COUNT` is used, so the filter
    applies in every invocation unless explicitly overridden.

    Returns a summary dict with counts for the run report.
    """
    client = new_github_client()
    now = datetime.datetime.now(datetime.UTC).isoformat()
    _t0 = time.monotonic()
    _meta_time = 0.0
    effective_max = MAX_SKILL_COUNT if max_skill_count is None else max_skill_count

    subdirs = sorted(
        d.name for d in base_dir.iterdir()
        if d.is_dir() and d.name.count("__") == 1
    )
    print(
        f"scanning by-source: {len(subdirs)} GitHub repo dirs"
        + (" (force)" if force else "")
        + f" (max-skill-count {effective_max})"
    )

    # Fetch all repo metadata concurrently (network-bound) before the loop.
    _tm = time.monotonic()
    sources = [dir_to_source(d) for d in subdirs]
    metas, missing = get_repo_metas(sources, client=client)
    _meta_time += time.monotonic() - _tm

    counters = {
        "skipped": 0,
        "updated": 0,
        "failed": 0,
        "gone": 0,
        "filtered": 0,
        "filtered_high_skill": 0,
        "total_skills": 0,
    }
    scanned: dict[str, JSON] = {}

    # Repo-level scanning is I/O-bound: overlap network waits across repos.
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as ex:
        futures = [
            ex.submit(
                _scan_one_repo,
                dir_name,
                force=force,
                now=now,
                base_dir=base_dir,
                metas=metas,
                missing=missing,
                client=client,
                counters=counters,
                max_skill_count=effective_max,
            )
            for dir_name in subdirs
        ]
        for fut in futures:
            res = fut.result()  # propagate unexpected exceptions
            if res is not None:
                scanned[res["source"]] = res

    # Original scan order follows the fetch order (skills.sh ranking), i.e. the
    # order in which each source first appeared in fetched-skills.jsonl. Build a
    # stable, deduplicated source list from that file.
    fetch_order: dict[str, int] = {}
    for rec in read_jsonl(FETCHED_SKILLS):
        src = str(rec.get("source", "")).strip()
        if src and src not in fetch_order:
            fetch_order[src] = len(fetch_order)

    def _orig_key(r: JSON) -> tuple[int, str]:
        src = r.get("source", "")
        return (fetch_order.get(src, len(fetch_order)), src)

    # Keep every scanned repo, ordered by fetch order (sources not present in
    # the fetch file sink to the end, alphabetical as a tie-breaker).
    repos = sorted(scanned.values(), key=_orig_key)
    # Repos without a star count sink to the end when sorted descending.
    repos_by_stars = sorted(
        repos, key=lambda r: r.get("stars") or 0, reverse=True
    )
    # Repos without a skillCount sink to the end when sorted descending.
    repos_by_skillcount = sorted(
        repos, key=lambda r: r.get("skillCount") or 0, reverse=True
    )

    # scanned-repos.jsonl is the raw scan order; the others are sorted views.
    write_jsonl(SCANNED_REPOS, repos)
    write_jsonl(SCANNED_REPOS_BY_STARS, repos_by_stars)
    write_jsonl(SCANNED_REPOS_BY_SKILLCOUNT, repos_by_skillcount)
    print(
        f"scan done: skipped {counters['skipped']} unchanged, "
        f"updated {counters['updated']}, failed {counters['failed']}, "
        f"gone {counters['gone']}, filtered {counters['filtered']} "
        f"(high-skill {counters['filtered_high_skill']})."
    )
    print(
        f"wrote {len(repos)} repos -> "
        f"{SCANNED_REPOS.name} (scan order), "
        f"{SCANNED_REPOS_BY_STARS.name} (by stars), "
        f"{SCANNED_REPOS_BY_SKILLCOUNT.name} (by skillCount)"
    )
    _total = time.monotonic() - _t0
    print(
        f"[timer] scan: total={_total:.1f}s "
        f"meta={_meta_time:.1f}s other={_total - _meta_time:.1f}s"
    )
    summary = {
        "repos_total": len(subdirs),
        "repos_skipped": counters["skipped"],
        "repos_updated": counters["updated"],
        "repos_failed": counters["failed"],
        "repos_gone": counters["gone"],
        "repos_filtered": counters["filtered"],
        "repos_filtered_high_skill": counters["filtered_high_skill"],
        "skills_scanned": counters["total_skills"],
    }
    return summary


def _summarize_repo(repo_dir: Path, meta_path: Path, source: str) -> JSON:
    """Read a repo's persisted meta + skills into a single summary record."""
    from .io_utils import read_jsonl

    meta = read_json(meta_path, default={}) or {}
    skills = read_jsonl(repo_dir / SCANNED_FILE)
    return {
        "source": source,
        "pushedAt": meta.get("pushedAt"),
        "stars": meta.get("stars"),
        "skillCount": meta.get("skillCount", len(skills)),
        "skills": [s["path"] for s in skills],
    }
