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


OWNERS = {f"owner{i}/repo{i}": (f"2024-01-0{i}T00:00:0{i}Z", "main") for i in range(1, 7)}


def _make_by_source(base_dir: Path) -> None:
    """Create 6 repo dirs; mark the first 3 as up-to-date (cached)."""
    for i, (source, (pushed, _branch)) in enumerate(OWNERS.items(), start=1):
        repo_dir = base_dir / config.source_to_dir(source)
        repo_dir.mkdir(parents=True, exist_ok=True)
        # Every repo already has a previous cache. Repos 4-6 will be "stale"
        # (pushedAt mismatch) so they get rescanned; 1-3 stay up-to-date.
        prev_pushed = pushed if i <= 3 else "1999-01-01T00:00:00Z"
        (repo_dir / config.META_FILE).write_text(
            f'{{"pushedAt": "{prev_pushed}", "schemaVersion": {config.SCHEMA_VERSION}, '
            f'"skillCount": 1, "blobShas": {{}}}}'
        )
        (repo_dir / config.SCANNED_FILE).write_text(
            '{"path": "skills/a", "description": "cached"}\n'
        )


@pytest.fixture
def patched(monkeypatch, tmp_path):
    base_dir = tmp_path / "by-source"
    _make_by_source(base_dir)
    # Redirect the global scanned-repos summary into tmp. scan.py binds
    # SCANNED_REPOS at import time, so patch the attribute on that module.
    scanned_repos = tmp_path / "scanned-repos.jsonl"
    monkeypatch.setattr(scan_mod, "SCANNED_REPOS", scanned_repos)

    seen_threads: set[int] = set()
    lock = threading.Lock()

    def fake_metas(sources, *, client=None, max_workers=8):
        with lock:
            for _ in sources:
                seen_threads.add(threading.get_ident())
        return {s: OWNERS[s] for s in sources if s in OWNERS}

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
        skills = read_jsonl(repo_dir / config.SCANNED_FILE)
        assert skills == [{"path": "skills/a", "description": "desc for a"}]
    # Up-to-date repos (1-3) keep their cached description untouched.
    for i in range(1, 4):
        source = f"owner{i}/repo{i}"
        repo_dir = base_dir / config.source_to_dir(source)
        skills = read_jsonl(repo_dir / config.SCANNED_FILE)
        assert skills == [{"path": "skills/a", "description": "cached"}]


def test_scan_force_rescans_everything(patched):
    base_dir, _seen, _sr = patched
    summary = scan_repositories(force=True, base_dir=base_dir)
    assert summary["repos_skipped"] == 0
    assert summary["repos_updated"] == 6
    assert summary["skills_scanned"] == 6
