"""Tests for the CLI entry point (no network required)."""

from __future__ import annotations

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
    """`update` calls fetch -> scan -> index with args forwarded correctly."""
    calls: list[tuple[str, dict]] = []

    def fake_fetch(*, max_pages: int = 0, token: str = "") -> list:
        calls.append(("fetch", {"max_pages": max_pages}))
        return []

    def fake_scan(*, force: bool = False, base_dir=None) -> None:
        calls.append(("scan", {"force": force}))

    def fake_index(*, base_dir=None) -> list:
        calls.append(("index", {}))
        return []

    monkeypatch.setattr(cli, "run_fetch", fake_fetch)
    monkeypatch.setattr(cli, "scan_repositories", fake_scan)
    monkeypatch.setattr(cli, "run_index", fake_index)

    assert cli.main(["update", "--pages", "1", "--force"]) == 0

    assert [c[0] for c in calls] == ["fetch", "scan", "index"]
    assert calls[0][1] == {"max_pages": 1}
    assert calls[1][1] == {"force": True}


def test_update_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without flags, update uses max_pages=0 (all) and force=False."""
    seen: dict[str, dict] = {}

    def fake_fetch(*, max_pages: int = 0, token: str = "") -> list:
        seen["fetch"] = {"max_pages": max_pages}
        return []

    def fake_scan(*, force: bool = False, base_dir=None) -> None:
        seen["scan"] = {"force": force}

    def fake_index(*, base_dir=None) -> list:
        return []

    monkeypatch.setattr(cli, "run_fetch", fake_fetch)
    monkeypatch.setattr(cli, "scan_repositories", fake_scan)
    monkeypatch.setattr(cli, "run_index", fake_index)

    assert cli.main(["update"]) == 0
    assert seen == {"fetch": {"max_pages": 0}, "scan": {"force": False}}


def test_unknown_command_returns_error() -> None:
    with pytest.raises(SystemExit):
        cli.main(["bogus"])
