"""Tests for the pure-logic helpers (no network required)."""

from __future__ import annotations

from pathlib import Path

from skills_index import config
from skills_index.fetch import filter_github
from skills_index.github import _parse_skill_dirs
from skills_index.io_utils import read_jsonl, write_jsonl


def test_source_dir_roundtrip() -> None:
    assert config.source_to_dir("vercel-labs/skills") == "vercel-labs__skills"
    assert config.dir_to_source("vercel-labs__skills") == "vercel-labs/skills"


def test_source_dir_only_first_sep_split() -> None:
    # source is always `owner/repo`; deeper slashes are NOT supported and
    # the mapping is only guaranteed lossless for a single separator.
    assert config.source_to_dir("a/b/c") == "a__b__c"
    assert config.dir_to_source("a__b__c") == "a/b__c"


def test_is_github_source() -> None:
    assert config.is_github_source("owner/repo")
    assert not config.is_github_source("https://example.com/owner/repo")
    assert not config.is_github_source("not-a-source")


def test_filter_github_whitelists_fields() -> None:
    skills = [
        {"source": "owner/repo", "skillId": "x", "name": "X", "installs": 1, "extra": "drop"},
        {"source": "https://other.com/x/y", "skillId": "y"},
    ]
    kept, dropped = filter_github(skills)
    assert dropped == 1
    assert len(kept) == 1
    assert "extra" not in kept[0]
    assert set(kept[0]) <= config.KEEP_FIELDS


def test_jsonl_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "out.jsonl"
    records = [{"a": 1}, {"b": "二"}]
    write_jsonl(path, records)
    assert read_jsonl(path) == records


def test_read_jsonl_missing_file(tmp_path: Path) -> None:
    assert read_jsonl(tmp_path / "nope.jsonl") == []


def test_parse_skill_dirs() -> None:
    tree = [
        {"type": "blob", "path": "skills/foo/SKILL.md"},
        {"type": "blob", "path": "skills/bar/baz/SKILL.md"},
        {"type": "blob", "path": "skills/foo/README.md"},
        {"type": "tree", "path": "skills/foo"},
    ]
    dirs = _parse_skill_dirs(tree)
    assert dirs == {"foo": "skills/foo", "baz": "skills/bar/baz"}
