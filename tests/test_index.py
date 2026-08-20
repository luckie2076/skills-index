"""Tests for the index merge step (no network required)."""

from __future__ import annotations

from pathlib import Path

import pytest

from skills_index import index as index_mod
from skills_index.io_utils import read_jsonl, write_jsonl


def _setup_data(
    tmp_path: Path, *, fetched: list[dict], scanned: dict[str, list[dict]]
) -> tuple[Path, Path, Path]:
    """Create data/fetched-skills.jsonl + data/by-source/<dir>/scanned.jsonl.

    Returns ``(fetched_path, index_path, by_source_dir)``.
    """
    data = tmp_path / "data"
    by_source = data / "by-source"
    by_source.mkdir(parents=True)
    fetched_path = data / "fetched-skills.jsonl"
    write_jsonl(fetched_path, fetched)
    for dir_name, records in scanned.items():
        gh = by_source / dir_name
        gh.mkdir()
        write_jsonl(gh / "scanned.jsonl", records)
    return fetched_path, data / "index.jsonl", by_source


def _patch_paths(monkeypatch: pytest.MonkeyPatch, fetched_path: Path, index_path: Path) -> None:
    monkeypatch.setattr(index_mod, "FETCHED_SKILLS", fetched_path)
    monkeypatch.setattr(index_mod, "INDEX_JSONL", index_path)


def test_run_index_merges_scanned_into_fetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetched = [
        {"source": "owner/repo", "skillId": "a", "installs": 10},
        {"source": "owner/repo", "skillId": "b", "installs": 20},
    ]
    scanned = {"owner__repo": [{"path": "skills/a", "description": "A"}]}
    fetched_path, index_path, by_source = _setup_data(tmp_path, fetched=fetched, scanned=scanned)
    _patch_paths(monkeypatch, fetched_path, index_path)

    records, summary = index_mod.run_index(base_dir=by_source)

    # `b` only exists in skills.sh, not in the repo scan -> dropped.
    assert records == [
        {"source": "owner/repo", "skillId": "a", "installs": 10,
         "path": "skills/a", "description": "A"}
    ]
    assert read_jsonl(index_path) == records
    assert summary["index"] == 1
    assert summary["scanned_merged"] == 1
    assert summary["not_in_repo"] == 1
    assert summary["orphans"] == 0


def test_run_index_keeps_fetched_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fetched = [
        {"source": "o/r", "skillId": "b", "installs": 2},
        {"source": "o/r", "skillId": "a", "installs": 1},
        {"source": "o/r", "skillId": "c", "installs": 3},
    ]
    # scanned order is deliberately the reverse of fetched order.
    scanned = {
        "o__r": [
            {"path": "skills/b", "description": "B"},
            {"path": "skills/a", "description": "A"},
        ]
    }
    fetched_path, index_path, by_source = _setup_data(tmp_path, fetched=fetched, scanned=scanned)
    _patch_paths(monkeypatch, fetched_path, index_path)

    records, summary = index_mod.run_index(base_dir=by_source)

    assert [r["skillId"] for r in records] == ["b", "a"]
    assert summary["not_in_repo"] == 1  # `c` dropped


def test_run_index_excludes_orphans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fetched = [{"source": "owner/repo", "skillId": "a", "installs": 1}]
    scanned = {
        "owner__repo": [
            {"path": "skills/a", "description": "A"},
            {"path": "skills/gh-only", "description": "not on skills.sh"},
        ]
    }
    fetched_path, index_path, by_source = _setup_data(tmp_path, fetched=fetched, scanned=scanned)
    _patch_paths(monkeypatch, fetched_path, index_path)

    records, summary = index_mod.run_index(base_dir=by_source)

    assert [r["skillId"] for r in records] == ["a"]
    assert summary["orphans"] == 1
    assert summary["index"] == 1


def test_run_index_empty_fetched_writes_empty_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fetched_path, index_path, by_source = _setup_data(tmp_path, fetched=[], scanned={})
    _patch_paths(monkeypatch, fetched_path, index_path)

    records, summary = index_mod.run_index(base_dir=by_source)

    assert records == []
    assert read_jsonl(index_path) == []
    assert "no fetched data" in capsys.readouterr().out
