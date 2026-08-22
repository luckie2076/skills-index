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
         "weeklyInstalls": [], "path": "skills/a", "description": "A"}
    ]
    assert read_jsonl(index_path) == records
    assert summary["index"] == 1
    assert summary["scanned_merged"] == 1
    assert summary["not_in_repo"] == 1
    assert summary["scan_only"] == 0


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


def test_run_index_includes_scan_only_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """仓库有、榜单无的技能仍入索引，installs/weeklyInstalls 置空，追加在末尾。"""
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

    assert records == [
        {"source": "owner/repo", "skillId": "a", "installs": 1,
         "weeklyInstalls": [], "path": "skills/a", "description": "A"},
        {"source": "owner/repo", "skillId": "gh-only", "installs": 0,
         "weeklyInstalls": [], "path": "skills/gh-only",
         "description": "not on skills.sh"},
    ]
    assert summary["scan_only"] == 1
    assert summary["index"] == 2


def test_run_index_empty_fetched_still_indexes_scanned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """fetched 数据缺失时仍以扫描为基准输出，元数据全部置空。"""
    fetched = []
    scanned = {"owner__repo": [{"path": "skills/a", "description": "A"}]}
    fetched_path, index_path, by_source = _setup_data(tmp_path, fetched=fetched, scanned=scanned)
    _patch_paths(monkeypatch, fetched_path, index_path)

    records, summary = index_mod.run_index(base_dir=by_source)

    assert records == [
        {"source": "owner/repo", "skillId": "a", "installs": 0,
         "weeklyInstalls": [], "path": "skills/a", "description": "A"}
    ]
    assert read_jsonl(index_path) == records
    assert summary["scan_only"] == 1
    assert summary["not_in_repo"] == 0
    assert "no fetched data" in capsys.readouterr().out


def test_run_index_empty_fetched_writes_empty_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fetched_path, index_path, by_source = _setup_data(tmp_path, fetched=[], scanned={})
    _patch_paths(monkeypatch, fetched_path, index_path)

    records, summary = index_mod.run_index(base_dir=by_source)

    assert records == []
    assert read_jsonl(index_path) == []
    assert "no fetched data" in capsys.readouterr().out


def test_run_index_dedups_identical_skills_across_repos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同 skillId + 同 description 的跨仓库重复只保留 installs 更高者；
    description 不同的同名技能（真实存在的不同实现）不受影响。"""
    fetched = [
        {"source": "a/r", "skillId": "x", "installs": 10},
        {"source": "b/r", "skillId": "x", "installs": 20},
        {"source": "c/r", "skillId": "x", "installs": 5},
    ]
    scanned = {
        "a__r": [{"path": "skills/x", "description": "same"}],
        "b__r": [{"path": "skills/x", "description": "same"}],
        # 同名但不同实现（description 不同）-> 两个不同技能，都保留。
        "c__r": [{"path": "skills/x", "description": "different"}],
    }
    fetched_path, index_path, by_source = _setup_data(tmp_path, fetched=fetched, scanned=scanned)
    _patch_paths(monkeypatch, fetched_path, index_path)

    records, summary = index_mod.run_index(base_dir=by_source)

    assert summary["deduped_skills"] == 1
    assert summary["index"] == 2
    # 保留 installs 更高的 b/r；description 不同的 c/r 不受影响。
    assert [r["source"] for r in records] == ["b/r", "c/r"]
    assert records[0]["installs"] == 20
    assert records[0]["description"] == "same"


def test_run_index_dedup_keeps_first_on_equal_installs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """installs 相同时保留 fetch 顺序靠前者（skills.sh 排名更高）。"""
    fetched = [
        {"source": "first/r", "skillId": "x", "installs": 7},
        {"source": "second/r", "skillId": "x", "installs": 7},
    ]
    scanned = {
        "first__r": [{"path": "skills/x", "description": "same"}],
        "second__r": [{"path": "skills/x", "description": "same"}],
    }
    fetched_path, index_path, by_source = _setup_data(tmp_path, fetched=fetched, scanned=scanned)
    _patch_paths(monkeypatch, fetched_path, index_path)

    records, summary = index_mod.run_index(base_dir=by_source)

    assert summary["deduped_skills"] == 1
    assert [r["source"] for r in records] == ["first/r"]


def test_run_index_no_dedup_on_empty_description(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """description 为空表示未知而非相同：不参与去重。"""
    fetched = [
        {"source": "a/r", "skillId": "x", "installs": 10},
        {"source": "b/r", "skillId": "x", "installs": 20},
    ]
    scanned = {
        "a__r": [{"path": "skills/x", "description": ""}],
        "b__r": [{"path": "skills/x", "description": ""}],
    }
    fetched_path, index_path, by_source = _setup_data(tmp_path, fetched=fetched, scanned=scanned)
    _patch_paths(monkeypatch, fetched_path, index_path)

    records, summary = index_mod.run_index(base_dir=by_source)

    assert summary["deduped_skills"] == 0
    assert len(records) == 2
