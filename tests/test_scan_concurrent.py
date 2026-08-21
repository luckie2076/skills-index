"""End-to-end test for concurrent repo scanning (no real network).

Mocks GitHub network calls and drives `scan_repositories` against a temp
`by-source` tree, asserting that concurrency produces correct, complete
per-repo artifacts and run-summary counts.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

import skills_index.config as config
import skills_index.scan as scan_mod
from skills_index.io_utils import read_json, read_jsonl
from skills_index.scan import scan_repositories

OWNERS = {
    f"owner{i}/repo{i}": (f"2024-01-0{i}T00:00:0{i}Z", "main", i * 100)
    for i in range(1, 7)
}


def _make_by_source(base_dir: Path) -> None:
    """Create 6 repo dirs; mark the first 3 as up-to-date (cached)."""
    for i, (source, (pushed, _branch, _stars)) in enumerate(OWNERS.items(), start=1):
        repo_dir = base_dir / config.source_to_dir(source)
        repo_dir.mkdir(parents=True, exist_ok=True)
        # Every repo already has a previous cache. Repos 4-6 will be "stale"
        # (pushedAt mismatch) so they get rescanned; 1-3 stay up-to-date.
        prev_pushed = pushed if i <= 3 else "1999-01-01T00:00:00Z"
        (repo_dir / config.META_FILE).write_text(
            f'{{"pushedAt": "{prev_pushed}", "schemaVersion": {config.SCHEMA_VERSION}, '
            f'"stars": {i * 100}, "skillCount": 1, "blobShas": {{}}}}'
        )
        (repo_dir / config.SCANNED_FILE).write_text(
            '{"path": "skills/a", "description": "cached"}\n'
        )


@pytest.fixture
def patched(monkeypatch, tmp_path):
    base_dir = tmp_path / "by-source"
    _make_by_source(base_dir)
    # Redirect the global scanned-repos summaries into tmp. scan.py binds
    # these names at import time, so patch the attributes on that module.
    scanned_repos = tmp_path / "scanned-repos.jsonl"
    monkeypatch.setattr(scan_mod, "SCANNED_REPOS", scanned_repos)
    monkeypatch.setattr(scan_mod, "SCANNED_REPOS_BY_STARS", tmp_path / "scanned-repos-by-stars.jsonl")
    monkeypatch.setattr(scan_mod, "SCANNED_REPOS_BY_SKILLCOUNT", tmp_path / "scanned-repos-by-skillcount.jsonl")

    seen_threads: set[int] = set()
    lock = threading.Lock()

    def fake_metas(sources, *, client=None, max_workers=8):
        with lock:
            for _ in sources:
                seen_threads.add(threading.get_ident())
        return {s: OWNERS[s] for s in sources if s in OWNERS}, set()

    def fake_blobs(source, branch, *, client=None):
        with lock:
            seen_threads.add(threading.get_ident())
        return {"a": ("skills/a", f"sha-{source}")}

    def fake_descs(source, fetch, *, client=None):
        with lock:
            seen_threads.add(threading.get_ident())
        return {name: f"desc for {name}" for name in fetch}

    monkeypatch.setattr(scan_mod, "get_repo_metas", fake_metas)
    monkeypatch.setattr(scan_mod, "get_skill_blobs", fake_blobs)
    monkeypatch.setattr(scan_mod, "get_skill_descriptions", fake_descs)
    return base_dir, seen_threads, scanned_repos


def test_scan_runs_concurrently_and_marks_threads(patched):
    base_dir, seen_threads, scanned_repos = patched
    summary = scan_repositories(base_dir=base_dir)
    # At least 2 distinct threads did the GitHub work (proves concurrency).
    assert len(seen_threads) >= 2
    # repo metadata fetch + per-repo blob/desc work all happened off main thread.
    assert summary["repos_total"] == 6
    assert summary["repos_skipped"] == 3
    assert summary["repos_updated"] == 3
    assert summary["repos_failed"] == 0
    assert summary["skills_scanned"] == 3
    assert scanned_repos.exists()


def test_scan_writes_per_repo_artifacts(patched):
    base_dir, _seen, _sr = patched
    scan_repositories(base_dir=base_dir)
    # Stale repos (4-6) get rescanned -> fresh meta + scanned.jsonl.
    for i in range(4, 7):
        source = f"owner{i}/repo{i}"
        repo_dir = base_dir / config.source_to_dir(source)
        meta = read_json(repo_dir / config.META_FILE)
        assert meta["pushedAt"] == OWNERS[source][0]  # updated to new pushed
        assert meta["stars"] == OWNERS[source][2]  # stargazers persisted
        skills = read_jsonl(repo_dir / config.SCANNED_FILE)
        assert skills == [{"path": "skills/a", "description": "desc for a"}]
    # Up-to-date repos (1-3) keep their cached description untouched.
    for i in range(1, 4):
        source = f"owner{i}/repo{i}"
        repo_dir = base_dir / config.source_to_dir(source)
        meta = read_json(repo_dir / config.META_FILE)
        assert meta["stars"] == OWNERS[source][2]
        skills = read_jsonl(repo_dir / config.SCANNED_FILE)
        assert skills == [{"path": "skills/a", "description": "cached"}]
    # The per-repo summary (scanned-repos.jsonl) records stars too.
    repos = read_jsonl(scan_mod.SCANNED_REPOS)
    assert len(repos) == 6
    for rec in repos:
        source = rec["source"]
        assert rec["stars"] == OWNERS[source][2]


def test_scan_force_rescans_everything(patched):
    base_dir, _seen, _sr = patched
    summary = scan_repositories(force=True, base_dir=base_dir)
    assert summary["repos_skipped"] == 0
    assert summary["repos_updated"] == 6
    assert summary["skills_scanned"] == 6


def test_scan_removes_stale_data_for_missing_repo(monkeypatch, tmp_path):
    base_dir = tmp_path / "by-source"
    ghost = "ghost/removed"
    repo_dir = base_dir / config.source_to_dir(ghost)
    repo_dir.mkdir(parents=True)
    # Stale cache from a previous run when the repo still existed.
    (repo_dir / config.META_FILE).write_text(
        f'{{"pushedAt": "2000-01-01T00:00:00Z", "schemaVersion": {config.SCHEMA_VERSION}}}'
    )
    (repo_dir / config.SCANNED_FILE).write_text(
        '{"path": "skills/a", "description": "stale"}\n'
    )
    scanned_repos = tmp_path / "scanned-repos.jsonl"
    monkeypatch.setattr(scan_mod, "SCANNED_REPOS", scanned_repos)
    # The repo is definitively gone (404): meta fetch returns it as missing.
    monkeypatch.setattr(
        scan_mod,
        "get_repo_metas",
        lambda sources, *, client=None, max_workers=8: ({}, {ghost}),
    )

    def _fail(*args, **kwargs):  # noqa: ARG002
        raise AssertionError("must not scan a repo that is gone (404)")

    monkeypatch.setattr(scan_mod, "get_skill_blobs", _fail)
    monkeypatch.setattr(scan_mod, "get_skill_descriptions", _fail)

    summary = scan_repositories(base_dir=base_dir)

    assert summary["repos_total"] == 1
    assert summary["repos_gone"] == 1
    assert summary["repos_failed"] == 0
    assert not repo_dir.exists()  # stale scan data removed
    assert read_jsonl(scanned_repos) == []  # repo not recorded


def test_scan_filters_low_star_repos(patched):
    """Repos below --min-stars are dropped and their stale cache removed.

    owner1..owner3 are up-to-date (cached), owner4..owner6 are stale. With
    min_stars=150, owner1 (100) and owner2 (200<250) ... owner1 only is below;
    verify both below-threshold (owner1=100) and an up-to-date cached repo
    (owner1, owner2) lose their cache so they cannot leak into the index.
    """
    base_dir, _seen, scanned_repos = patched
    # owner1=100, owner2=200, owner3=300, owner4=400, owner5=500, owner6=600
    summary = scan_repositories(min_stars=250, base_dir=base_dir)
    # owner1/owner2 below threshold -> filtered; owner3+ kept.
    assert summary["repos_filtered"] == 2
    assert summary["repos_updated"] == 3  # owner4/5/6 stale rescanned
    assert summary["repos_skipped"] == 1  # only owner3 stays up-to-date
    assert summary["skills_scanned"] == 3
    # Below-threshold repos' cache dirs are removed entirely.
    assert not (base_dir / config.source_to_dir("owner1/repo1")).exists()
    assert not (base_dir / config.source_to_dir("owner2/repo2")).exists()
    # Kept repos retain their cache.
    assert (base_dir / config.source_to_dir("owner3/repo3")).exists()
    # Filtered repos are absent from the per-repo summary.
    repos = read_jsonl(scanned_repos)
    kept = {r["source"] for r in repos}
    assert kept == {"owner3/repo3", "owner4/repo4", "owner5/repo5", "owner6/repo6"}


def test_is_missing_repo_detects_404_in_cause_chain():
    import httpx

    from skills_index.github import _is_missing_repo
    from skills_index.http import HttpError

    req = httpx.Request("GET", "https://api.github.com/repos/o/r")
    resp404 = httpx.Response(404, request=req)
    inner404 = httpx.HTTPStatusError("404 on /repos/o/r", request=req, response=resp404)
    err404 = HttpError("failed")
    err404.__cause__ = inner404
    assert _is_missing_repo(err404) is True

    resp500 = httpx.Response(500, request=req)
    inner500 = httpx.HTTPStatusError("500 on /repos/o/r", request=req, response=resp500)
    err500 = HttpError("failed")
    err500.__cause__ = inner500
    assert _is_missing_repo(err500) is False
    assert _is_missing_repo(RuntimeError("boom")) is False
