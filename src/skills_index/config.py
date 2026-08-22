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
FETCHED_SKILLS = DATA_DIR / "fetched-skills.jsonl"
# 最终合并产物（fetch + scan 结合），由 `index` 命令生成（以 skill 为单位平铺）
INDEX_JSONL = DATA_DIR / "index.jsonl"
# scan 的汇总产物：原始扫描顺序（每个仓库一行，含更新时间、技能数量、技能详情）；由 `scan` 命令生成
SCANNED_REPOS = DATA_DIR / "scanned-repos.jsonl"
# 按 star 数降序排列的扫描汇总
SCANNED_REPOS_BY_STARS = DATA_DIR / "scanned-repos-by-stars.jsonl"
# 按安装 skills 技能数（skillCount）降序排列的扫描汇总
SCANNED_REPOS_BY_SKILLCOUNT = DATA_DIR / "scanned-repos-by-skillcount.jsonl"

# 仓库 skillCount 过滤上限：scan 与 index 均会丢弃 skillCount > MAX_SKILL_COUNT
# 的仓库（例如聚合型 / awesome-list 类仓库会捆绑过量技能，稀释索引质量）。
# 设为 0 可关闭该上限。
MAX_SKILL_COUNT = 500

# Skill 级过滤（scan 阶段）之一：结构性内部目录。SKILL.md 所在路径的任一
# 非文件名目录段命中以下目录则视为非公开技能（测试 / 示例 / 构建产物等），
# 不写入 scanned.jsonl。只匹配中间目录段、整段精确比较且大小写不敏感，
# 因此名为 test / template / e2e 的真实技能（最后一段是技能自身目录名）
# 不会被误伤。
SKILL_EXCLUDE_DIRS: frozenset[str] = frozenset({
    "test", "tests", "__tests__", "spec", "e2e",
    "example", "examples", "sample", "samples", "demo", "demos",
    "fixture", "fixtures", "mock", "mocks", "stub", "stubs",
    "template", "templates", "scaffold", "boilerplate",
    "doc", "docs",
    "dist", "build", "out", "node_modules", "vendor", "third_party",
})

# Skill 级过滤之二：状态词（与 HIDDEN_FRONTMATTER_MARKERS 语义对齐）。
# 任一路径段命中即排除，含技能自身目录名——目录或技能名本身为
# deprecated / hidden / private 等即宣示非公开（如 skills/deprecated/foo
# 或名为 hidden 的技能）；现实中不存在恰好以这些词命名的公开技能
# （数据回放零命中，纯预防性规则）。
SKILL_EXCLUDE_ANY_DIRS: frozenset[str] = frozenset({
    "deprecated", "hidden", "private", "internal", "obsolete",
})

# 隐藏目录段（如 .github / .devcontainer）默认视为仓库配置而排除；但紧跟
# skills 段的隐藏根（.claude/skills、.agents/skills 等各 agent 工具的公开
# 技能标准位置）以及 .skills 根本身保留（见 is_internal_skill_path）。
# .github 恒为仓库配置，即使形如 .github/skills 也不算公开。

# frontmatter 中标记技能为非公开的字段（值为真即排除，如 true / yes / 1）。
# 识别生态常见别名，不发明新标准；另支持 public: false。
HIDDEN_FRONTMATTER_MARKERS: tuple[str, ...] = (
    "deprecated", "hidden", "private", "internal", "obsolete",
)


def is_internal_skill_path(rel: str) -> bool:
    """True if a SKILL.md lives in a repo-internal (non user-facing) directory."""
    segs = rel.split("/")
    for seg in segs:  # 状态词匹配任意段，含技能自身目录名
        if seg.lower() in SKILL_EXCLUDE_ANY_DIRS:
            return True
    for i, seg in enumerate(segs[:-1]):  # 结构词只看中间目录段，不含技能名
        low = seg.lower()
        if low in SKILL_EXCLUDE_DIRS:
            return True
        if seg.startswith("."):
            next_low = segs[i + 1].lower()
            public_root = (next_low == "skills" or low == ".skills") and low != ".github"
            if not public_root:
                return True
    return False

# --- External endpoints ---
SKILLS_API = "https://skills.sh/api/skills/all-time"
GITHUB_API = "https://api.github.com"

# --- File names produced per repository under data/by-source/<owner>__<repo>/ ---
FETCHED_FILE = "fetched.jsonl"
SCANNED_FILE = "scanned.jsonl"
META_FILE = "meta.json"

# Bump when the scan output format changes so stale caches are rebuilt once.
# v4: skill 级过滤（内部路径 + 非公开 frontmatter 标记）——v3 缓存的
# scanned.jsonl 可能含非公开技能，需要重建。
SCHEMA_VERSION = 4

# Fields kept from the skills.sh payload. No URL is persisted: consumers
# reconstruct the GitHub directory URL from `source` + `path` (see README).
KEEP_FIELDS: set[str] = {"source", "skillId", "installs", "weeklyInstalls"}

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
    path: str


def source_to_dir(source: str) -> str:
    """Map `owner/repo` to a flat, reversible directory name `owner__repo`."""
    return source.replace("/", DIR_SEP)


def dir_to_source(dir_name: str) -> str:
    """Inverse of :func:`source_to_dir` (only the first separator is split)."""
    return dir_name.replace(DIR_SEP, "/", 1)


def iter_repo_dirs(base_dir: Path) -> list[str]:
    """Return sorted repo dir names under `base_dir` (exactly one ``DIR_SEP``).

    A real GitHub source is ``owner/repo`` (single slash) -> ``owner__repo``
    (single ``DIR_SEP``). Deeper slashes are not supported and are skipped.
    """
    if not base_dir.exists():
        return []
    return sorted(
        d.name
        for d in base_dir.iterdir()
        if d.is_dir() and d.name.count(DIR_SEP) == 1
    )


def is_github_source(source: str) -> bool:
    return bool(GITHUB_SOURCE.match(source.strip()))


def load_github_token() -> str:
    """Return a GitHub token: prefer `GH_PAT`, then `GITHUB_TOKEN`, then `.env`.

    `GH_PAT` is a personal access token (5000 req/h) recommended for CI; the
    Actions-provided `GITHUB_TOKEN` is capped at 1000 req/h per repository.
    """
    for var in ("GH_PAT", "GITHUB_TOKEN"):
        token = os.environ.get(var, "").strip()
        if token:
            return token
    env_file = ROOT / ".env"
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            for var in ("GH_PAT=", "GITHUB_TOKEN="):
                if line.startswith(var):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return ""


# A minimal JSON-able record alias used by IO helpers.
Record = dict[str, Any]
