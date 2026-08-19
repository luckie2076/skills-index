# Skills Index

这是一个 [skills.sh](https://skills.sh) 技能的索引，让用户可以在一处查看每个技能的 `source` / `skillId` / `installs` / `weeklyInstalls` / `url`（来自 skills.sh），以及经仓库扫描得到的完整 GitHub 目录 URL `url`。

## 核心功能

### 1. 获取 skills.sh 数据

- **极简说明**：从 skills.sh 公开 API 拉取「历史总榜」，得到每个技能的来源、安装量等原始信息。
- **对应命令**：`uv run skills-index fetch`
- **结果形状**：抓取到的每个仓库写入 `data/by-source/<owner>__<repo>/skills-sh.jsonl`，并汇总到 `data/skills-sh-all.jsonl`（注意：这是中间产物，不是最终索引）。每行一个 JSON 对象：

```json
{
  "source": "vercel-labs/skills",
  "skillId": "find-skills",
  "installs": 3005209,
  "weeklyInstalls": [
    113781, 109199, 109085, 115475, 107969, 101120, 96861, 93130
  ],
  "url": "https://skills.sh/s/vercel-labs/skills/find-skills"
}
```

### 2. 扫描 github 仓库（获取具体 github url）

- **极简说明**：按 `pushed_at` 增量扫描各 GitHub 仓库，找出其中真正的 `SKILL.md` 技能定义（跳过未变更的仓库）。这一步会扫描出一个仓库内的**所有 skills**，并直接拼出每个 skill 的完整 GitHub 目录 URL（字段名 `url`，由 `path` 拼成）——GitHub URL 是在这里产生的，无需额外解析。
- **对应命令**：`uv run skills-index scan`（加 `--force` 可强制全量重扫）
- **结果形状**：在每个仓库目录下输出 `skills-github.jsonl`（扫描发现的所有技能，含 `url` / `path`）与 `github-meta.json`（仓库元信息）；增量缓存的 `pushed_at` 直接读取各仓库已有的 `github-meta.json`，无需额外的全局状态文件。

```json
// skills-github.jsonl 中的一行
{
  "source": "vercel-labs/skills",
  "skillId": "find-skills",
  "name": "find-skills",
  "path": "skills/find-skills",
  "url": "https://github.com/vercel-labs/skills/tree/main/skills/find-skills"
}
```

```json
// github-meta.json
{ "branch": "main", "pushedAt": "2026-08-10T12:00:00Z", "skillCount": 12 }
```

### 3. 合并索引（结合前两步生成最终 index.jsonl）

- **极简说明**：把第 1 步的 skills.sh 原始数据（`skills-sh-all.jsonl`）与第 2 步扫描出的所有仓库技能（`skills-github.jsonl`）按 `source` + `skillId` 合并，生成最终索引 `data/index.jsonl`（以**技能**为单位平铺，每行一个完整技能记录，含 skills.sh 元信息 + 扫描得到的 `url` / `path`）。
- **对应命令**：`uv run skills-index index`
- **结果形状**：

```json
// index.jsonl —— 每行一个技能
{
  "source": "vercel-labs/skills",
  "skillId": "find-skills",
  "name": "find-skills",
  "installs": 3005209,
  "weeklyInstalls": [113781, 109199, 109085, 115475, 107969, 101120, 96861, 93130],
  "url": "https://github.com/vercel-labs/skills/tree/main/skills/find-skills",
  "path": "skills/find-skills"
}
```

## 完整数据布局

```
data/
  skills-sh-all.jsonl    # 第1步产物：skills.sh 原始数据汇总（中间产物）
  index.jsonl            # 第3步产物：合并后的最终索引（以 skill 为单位平铺）
  by-source/
    <owner>__<repo>/     # 双下划线是 '/' 的无损替换
      skills-sh.jsonl    # 该仓库的 skills.sh 原始数据
      skills-github.jsonl# 经 scan 发现的所有技能，含 url / path
      github-meta.json   # 分支 / pushedAt / skillCount（来自 GitHub）
```

> 说明：`fetch` 只保存 skills.sh 的原始字段（`source` / `skillId` / `name` / `installs` / `weeklyInstalls` / `url`），不解析、也不保存 GitHub URL；每个技能的 GitHub 目录 URL 由 `scan` 在扫描仓库目录树时产生，并在第 3 步 `index` 合并进 `index.jsonl`。`index.jsonl` 以技能为单位平铺，便于按 `skillId` 检索。

## 环境要求

- Python >= 3.11
- [`uv`](https://docs.astral.sh/uv/)

可选：设置 `GITHUB_TOKEN`（环境变量或 `.env` 文件）可将 GitHub 速率限制从 60 次/小时提升到 5000 次/小时，并校验确切的技能路径。

## 安装

```bash
uv sync
```

## 使用方法

```bash
# 1) 获取 skills.sh 数据，写入 data/skills-sh-all.jsonl + data/by-source/（仅原始字段）
uv run skills-index fetch

# 限制页数（适合快速冒烟测试）
uv run skills-index fetch --pages 1

# 2) 扫描 GitHub 仓库里的 SKILL.md，产出带 url 的技能（跳过 pushed_at 未变化的仓库）
uv run skills-index scan

# 强制完整重新扫描
uv run skills-index scan --force

# 3) 结合前两步，生成最终索引 data/index.jsonl
uv run skills-index index
```

## 开发

```bash
uv run ruff check .
uv run mypy src/skills_index
uv run pytest
```
