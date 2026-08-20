"""Tests for the CLI entry point (no network required)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skills_index import cli


def test_parser_has_all_subcommands() -> None:
    parser = cli.build_parser()
    # Every known command is registered as a subparser choice.
    assert set(parser._subparsers._group_actions[0].choices) == {  # type: ignore[attr-defined]
        "fetch",
        "scan",
        "index",
        "update",
    }


def test_update_runs_pipeline_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """`update` calls clean -> fetch -> scan -> index with args forwarded correctly."""
    calls: list[tuple[str, dict]] = []

    def fake_clean() -> None:
        calls.append(("clean", {}))

    def fake_fetch(*, max_pages: int = 0, token: str = "") -> tuple[list, dict]:
        calls.append(("fetch", {"max_pages": max_pages}))
        return [], {}

    def fake_scan(*, force: bool = False, base_dir=None) -> dict:
        calls.append(("scan", {"force": force}))
        return {}

    def fake_index(*, base_dir=None) -> tuple[list, dict]:
        calls.append(("index", {}))
        return [], {}

    monkeypatch.setattr(cli, "clean_workspace", fake_clean)
    monkeypatch.setattr(cli, "run_fetch", fake_fetch)
    monkeypatch.setattr(cli, "scan_repositories", fake_scan)
    monkeypatch.setattr(cli, "run_index", fake_index)

    assert cli.main(["update", "--pages", "1", "--force"]) == 0

    assert [c[0] for c in calls] == ["clean", "fetch", "scan", "index"]
    assert calls[1][1] == {"max_pages": 1}
    assert calls[2][1] == {"force": True}


def test_update_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without flags, update uses max_pages=0 (all) and force=False."""
    seen: dict[str, dict] = {}

    def fake_clean() -> None:
        seen["clean"] = {}

    def fake_fetch(*, max_pages: int = 0, token: str = "") -> tuple[list, dict]:
        seen["fetch"] = {"max_pages": max_pages}
        return [], {}

    def fake_scan(*, force: bool = False, base_dir=None) -> dict:
        seen["scan"] = {"force": force}
        return {}

    def fake_index(*, base_dir=None) -> tuple[list, dict]:
        return [], {}

    monkeypatch.setattr(cli, "clean_workspace", fake_clean)
    monkeypatch.setattr(cli, "run_fetch", fake_fetch)
    monkeypatch.setattr(cli, "scan_repositories", fake_scan)
    monkeypatch.setattr(cli, "run_index", fake_index)

    assert cli.main(["update"]) == 0
    assert seen == {
        "clean": {},
        "fetch": {"max_pages": 0},
        "scan": {"force": False},
    }


def test_clean_workspace_wipes_stale_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """clean_workspace removes root summaries and the whole per-source tree."""
    data = tmp_path / "data"
    by_source = data / "by-source"
    by_source.mkdir(parents=True)
    stale_repo = by_source / "owner__repo"
    stale_repo.mkdir()
    (stale_repo / "meta.json").write_text(json.dumps({"pushedAt": "x"}))
    (stale_repo / "scanned.jsonl").write_text("{}")
    (data / "fetched-skills.jsonl").write_text("{}")
    (data / "index.jsonl").write_text("{}")
    (data / "scanned-repos.jsonl").write_text("{}")

    monkeypatch.setattr(cli, "DATA_DIR", data)
    monkeypatch.setattr(cli, "BY_SOURCE_DIR", by_source)
    monkeypatch.setattr(cli, "FETCHED_SKILLS", data / "fetched-skills.jsonl")
    monkeypatch.setattr(cli, "INDEX_JSONL", data / "index.jsonl")
    monkeypatch.setattr(cli, "SCANNED_REPOS", data / "scanned-repos.jsonl")

    cli.clean_workspace()

    assert not (data / "fetched-skills.jsonl").exists()
    assert not (data / "index.jsonl").exists()
    assert not (data / "scanned-repos.jsonl").exists()
    # The per-source tree is wiped entirely (no stale repo dirs remain).
    assert list(by_source.iterdir()) == []


def test_unknown_command_returns_error() -> None:
    with pytest.raises(SystemExit):
        cli.main(["bogus"])
