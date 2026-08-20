"""Tests for `update` incremental semantics and stale-repo pruning."""

from __future__ import annotations

from pathlib import Path

import pytest

from skills_index import cli, fetch
from skills_index.config import source_to_dir


def test_prune_stale_repos_removes_only_unlisted(tmp_path: Path) -> None:
    """Dirs for sources missing from `sources` are removed; listed ones stay."""
    base = tmp_path / "by-source"
    keep = source_to_dir("keep/repo")
    stale = source_to_dir("gone/repo")
    for d in (keep, stale):
        (base / d).mkdir(parents=True)
        (base / d / "meta.json").write_text("{}")
    # Unrelated dirs / files are never touched.
    (base / "not_a_repo_dir").mkdir()
    (base / "file.txt").write_text("x")

    removed = fetch.prune_stale_repos({"keep/repo"}, base_dir=base)

    assert removed == 1
    assert (base / keep).exists()
    assert not (base / stale).exists()
    assert (base / "not_a_repo_dir").exists()
    assert (base / "file.txt").exists()


def test_prune_stale_repos_noop_when_nothing_stale(tmp_path: Path) -> None:
    """All on-disk dirs listed in `sources` -> nothing removed."""
    base = tmp_path / "by-source"
    keep = source_to_dir("keep/repo")
    (base / keep).mkdir(parents=True)
    (base / keep / "meta.json").write_text("{}")

    removed = fetch.prune_stale_repos({"keep/repo"}, base_dir=base)

    assert removed == 0
    assert (base / keep).exists()


def test_update_pages_takes_clean_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A partial fetch (--pages N) is not incremental: clean, no pruning."""
    seen: list[str] = []

    def fake_clean() -> None:
        seen.append("clean")

    def fake_fetch(*, max_pages: int = 0, token: str = "") -> tuple[list, dict]:
        seen.append(f"fetch:{max_pages}")
        return [], {}

    def fake_scan(*, force: bool = False, base_dir=None) -> dict:
        seen.append("scan")
        return {}

    def fake_index(*, base_dir=None) -> tuple[list, dict]:
        return [], {}

    def fake_prune(sources, base_dir=None) -> int:
        seen.append("prune")
        return 0

    monkeypatch.setattr(cli, "clean_workspace", fake_clean)
    monkeypatch.setattr(cli, "run_fetch", fake_fetch)
    monkeypatch.setattr(cli, "scan_repositories", fake_scan)
    monkeypatch.setattr(cli, "run_index", fake_index)
    monkeypatch.setattr(cli, "prune_stale_repos", fake_prune)

    assert cli.main(["update", "--pages", "1"]) == 0
    assert seen == ["clean", "fetch:1", "scan"]


def test_update_force_takes_clean_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """--force is not incremental: clean first, then rescan everything."""
    seen: list[str] = []

    def fake_clean() -> None:
        seen.append("clean")

    def fake_fetch(*, max_pages: int = 0, token: str = "") -> tuple[list, dict]:
        seen.append("fetch")
        return [], {}

    def fake_scan(*, force: bool = False, base_dir=None) -> dict:
        seen.append(f"scan:{force}")
        return {}

    def fake_index(*, base_dir=None) -> tuple[list, dict]:
        return [], {}

    def fake_prune(sources, base_dir=None) -> int:
        seen.append("prune")
        return 0

    monkeypatch.setattr(cli, "clean_workspace", fake_clean)
    monkeypatch.setattr(cli, "run_fetch", fake_fetch)
    monkeypatch.setattr(cli, "scan_repositories", fake_scan)
    monkeypatch.setattr(cli, "run_index", fake_index)
    monkeypatch.setattr(cli, "prune_stale_repos", fake_prune)

    assert cli.main(["update", "--force"]) == 0
    assert seen == ["clean", "fetch", "scan:True"]
