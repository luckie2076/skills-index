"""Tests for the pure-logic helpers (no network required)."""

from __future__ import annotations

from pathlib import Path

from skills_index import config
from skills_index.fetch import filter_github
from skills_index.github import (
    _parse_skill_blobs,
    _parse_skill_dirs,
    extract_description,
    get_skill_descriptions,
)
from skills_index.io_utils import read_jsonl, write_jsonl
from skills_index.scan import merge_skill_records, plan_blob_fetches


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
        {"type": "blob", "path": "skills/foo/SKILL.md", "sha": "abc"},
        {"type": "blob", "path": "skills/bar/baz/SKILL.md", "sha": "def"},
        {"type": "blob", "path": "skills/foo/README.md"},
        {"type": "tree", "path": "skills/foo"},
    ]
    dirs = _parse_skill_dirs(tree)
    assert dirs == {"foo": "skills/foo", "baz": "skills/bar/baz"}


def test_parse_skill_blobs_carries_sha() -> None:
    tree = [
        {"type": "blob", "path": "skills/foo/SKILL.md", "sha": "abc123"},
        {"type": "blob", "path": "skills/foo/README.md", "sha": "nope"},
        {"type": "tree", "path": "skills/foo"},
    ]
    assert _parse_skill_blobs(tree) == {"foo": ("skills/foo", "abc123")}


def test_extract_description_from_frontmatter() -> None:
    md = """---
name: find-skills
description: Discover and install agent skills
---

# Find Skills
"""
    assert extract_description(md) == "Discover and install agent skills"


def test_extract_description_multiline_block() -> None:
    md = """---
name: x
description: |
  First line
  Second line
---

Body
"""
    assert extract_description(md) == "First line\nSecond line"


def test_extract_description_missing_returns_empty() -> None:
    assert extract_description("no frontmatter here") == ""
    assert extract_description("---\nname: only-name\n---\nbody") == ""
    assert extract_description("---\ninvalid: [unclosed\n---") == ""


def test_plan_blob_fetches_incremental() -> None:
    blobs = {
        "a": ("skills/a", "sha1"),
        "b": ("skills/b", "sha2"),
        "c": ("skills/c", "sha3"),
    }
    prev = {"skills/a": "sha1", "skills/b": "old-sha"}
    # Only changed or new blobs are refetched; unchanged `a` is skipped.
    assert plan_blob_fetches(blobs, prev, force=False, schema_upgrade=False) == {
        "b": ("skills/b", "sha2"),
        "c": ("skills/c", "sha3"),
    }
    # force / schema upgrade refetch everything.
    assert plan_blob_fetches(blobs, prev, force=True, schema_upgrade=False) == blobs
    assert plan_blob_fetches(blobs, prev, force=False, schema_upgrade=True) == blobs


def test_merge_skill_records_incremental() -> None:
    blobs = {"a": ("skills/a", "sha1"), "b": ("skills/b", "sha2-new")}
    old = [
        {"path": "skills/a", "description": "old-a"},
        {"path": "skills/b", "description": "old-b"},
        {"path": "skills/removed", "description": "gone"},
    ]
    prev = {"skills/a": "sha1", "skills/b": "sha2-old", "skills/removed": "x"}
    merged = merge_skill_records(
        blobs, {"b": "new-b"}, old, prev, force=False, schema_upgrade=False
    )
    assert merged == [
        {"path": "skills/a", "description": "old-a"},
        {"path": "skills/b", "description": "new-b"},
    ]


def test_merge_skill_records_force_rebuilds_all() -> None:
    blobs = {"a": ("skills/a", "sha1")}
    old = [{"path": "skills/a", "description": "old-a"}]
    prev = {"skills/a": "sha1"}
    force_merged = merge_skill_records(
        blobs, {"a": "new-a"}, old, prev, force=True, schema_upgrade=False
    )
    assert force_merged == [{"path": "skills/a", "description": "new-a"}]
    upgraded = merge_skill_records(
        blobs, {"a": "new-a"}, old, prev, force=False, schema_upgrade=True
    )
    assert upgraded == [{"path": "skills/a", "description": "new-a"}]


def test_get_skill_descriptions_empty_subset_skips_network() -> None:
    assert get_skill_descriptions("owner/repo", {}) == {}
