"""Project-wide constants, paths, and shared types."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, TypedDict

# JSON payloads are dynamically shaped; we do not over-constrain them.
JSON = Any

# --- Paths (derived from this package location, no dependency on CWD) ---
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DATA_DIR = ROOT / "data"
BY_SOURCE_DIR = DATA_DIR / "by-source"

# fetch 的中间产物（skills.sh 原始数据汇总）；最终索引 index.jsonl 由 index 步骤生成
SKILLS_SH_ALL = DATA_DIR / "skills-sh-all.jsonl"
# 最终合并产物（fetch + scan 结合），由 `index` 命令生成（以 skill 为单位平铺）
INDEX_JSONL = DATA_DIR / "index.jsonl"
# scan 的汇总产物（每个仓库一行，含更新时间、技能数量、技能详情）；由 `scan` 命令生成
SCAN_ALL_JSONL = DATA_DIR / "scan-all.jsonl"

# --- External endpoints ---
SKILLS_API = "https://skills.sh/api/skills/all-time"
GITHUB_API = "https://api.github.com"

# --- File names produced per repository under data/by-source/<owner>__<repo>/ ---
SKILLS_SH_FILE = "skills-sh.jsonl"
GITHUB_FILE = "skills-github.jsonl"
REPO_META_FILE = "github-meta.json"

# Fields kept from the skills.sh payload.
KEEP_FIELDS: set[str] = {"source", "skillId", "installs", "weeklyInstalls", "url"}

# A GitHub source is `owner/repo` (contains a slash, is not a full URL).
GITHUB_SOURCE = re.compile(r"^[^/\s]+/[^/\s]+$")

# Directory separator replacement. Double underscore is safe because GitHub
# owners/repos never contain a run of two underscores, so the mapping is
# lossless: `owner/repo` <-> `owner__repo`.
DIR_SEP = "__"


class Skill(TypedDict, total=False):
    """A single skill record (subset of fields we persist)."""

    source: str
    skillId: str
    installs: int
    weeklyInstalls: list[int]
    url: str
    path: str


def source_to_dir(source: str) -> str:
    """Map `owner/repo` to a flat, reversible directory name `owner__repo`."""
    return source.replace("/", DIR_SEP)


def dir_to_source(dir_name: str) -> str:
    """Inverse of :func:`source_to_dir` (only the first separator is split)."""
    return dir_name.replace(DIR_SEP, "/", 1)


def is_github_source(source: str) -> bool:
    return bool(GITHUB_SOURCE.match(source.strip()))


def load_github_token() -> str:
    """Return a GitHub token: prefer the env var, then a local `.env` file."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return token
    env_file = ROOT / ".env"
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("GITHUB_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return ""


# A minimal JSON-able record alias used by IO helpers.
Record = dict[str, Any]
