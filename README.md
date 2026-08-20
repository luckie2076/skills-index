# Skills Index

[skills.sh](https://skills.sh) 技能的索引：在一处查看每个技能的 `source` / `skillId` / `installs` / `weeklyInstalls`（来自 skills.sh），以及经仓库扫描得到的 GitHub 仓库内相对路径 `path` 与技能说明 `description`。

---

## 数据发布（GitHub Releases）

索引数据**不提交进 git 主分支**（见 `.gitignore`）。数据由 GitHub Actions 每日（UTC 0 点）自动生成，并作为 Release 资产发布。消费方无需 clone 仓库，直接拉取最新快照即可。

`data.tar.gz` 同时充当下一轮 CI 增量扫描的缓存载体：main 分支每次运行前会从上一个 `data-` Release 恢复 `data/by-source/`（含 `meta.json` / `scanned.jsonl` 增量指纹），使跨 runner 的增量扫描真正生效。

### 发布的产物

每个 Release 包含：

| 文件 | 说明 |
| --- | --- |
| `data.tar.gz` | 完整数据快照（含 `by-source/` 下所有仓库的 fetched / scanned / meta） |
| `index.jsonl` | 合并后的最终索引（以**技能**为单位平铺，推荐直接消费这个） |
| `fetched-skills.jsonl` | skills.sh 原始数据汇总（中间产物） |
| `scanned-repos.jsonl` | 按仓库汇总的扫描结果（中间产物） |

`index.jsonl` 每行一个技能：

```json
{
  "source": "vercel-labs/skills",
  "skillId": "find-skills",
  "installs": 3005209,
  "weeklyInstalls": [113781, 109199, 109085, 115475, 107969, 101120, 96861, 93130],
  "path": "skills/find-skills",
  "description": "Discover and install agent skills"
}
```

> 索引不存储可直接访问的 `url` 字段；完整 GitHub 目录 URL 由 `source` + `path` 拼接得到：`https://github.com/<source>/tree/HEAD/<path>`（`HEAD` 恒指向默认分支，分支变更也不受影响）。

### 如何使用（消费数据）

```bash
# 拉取最新索引（稳定 URL，自动重定向到 latest）
curl -L -o index.jsonl \
  https://github.com/luckie2076/skills-index/releases/latest/download/index.jsonl

# 拉取完整数据快照
curl -L -o data.tar.gz \
  https://github.com/luckie2076/skills-index/releases/latest/download/data.tar.gz
```

也可在仓库 Releases 页面选择任意历史快照按需下载。保留最近 30 个 Release，超出部分自动清理。

---

## 工作原理

索引由三步流水线生成，最终合并为 `data/index.jsonl`。

### 1. 获取 skills.sh 数据（`fetch`）

- **极简说明**：从 skills.sh 公开 API 拉取「历史总榜」，得到每个技能的来源、安装量等原始信息。
- **对应命令**：`uv run skills-index fetch`
- **产物形状**：每个仓库写入 `data/by-source/<owner>__<repo>/fetched.jsonl`，并汇总到 `data/fetched-skills.jsonl`（中间产物，非最终索引）。每行一个 JSON 对象（`source` / `skillId` / `installs` / `weeklyInstalls`，不含 GitHub URL）：

```json
{
  "source": "vercel-labs/skills",
  "skillId": "find-skills",
  "installs": 3005209,
  "weeklyInstalls": [113781, 109199, 109085, 115475, 107969, 101120, 96861, 93130]
}
```

### 2. 扫描 GitHub 仓库（`scan`）

- **极简说明**：按 `pushed_at` 增量扫描各 GitHub 仓库，找出其中真正的 `SKILL.md` 技能定义（跳过未变更的仓库）。增量粒度是**文件级 blob sha**：`pushed_at` 变化的仓库，通过 Git Tree API 拿到全仓目录树，只对 sha 相比上次发生变化的 `SKILL.md` 重新拉取内容（Git Blob API）并解析 YAML frontmatter 提取 `description`，未变化的技能直接复用本地缓存；从目录树中消失的技能会被自动移除。每个技能只记录仓库内相对路径 `path`，完整 GitHub 目录 URL 由调用方用 `source` + `path` 拼接。
- **对应命令**：`uv run skills-index scan`（加 `--force` 可强制全量重扫；扫描产物格式升级时会自动触发一次性全量重扫）
- **产物形状**：在每个仓库目录下输出 `scanned.jsonl`（扫描发现的所有技能，含 `path` / `description`）与 `meta.json`（仓库元信息，含 `blobShas` 文件级增量指纹与 `schemaVersion`）。

```json
// scanned.jsonl 中的一行
{ "path": "skills/find-skills", "description": "Discover and install agent skills" }
```

```json
// meta.json
{
  "branch": "main",
  "pushedAt": "2026-08-10T12:00:00Z",
  "lastScanned": "2026-08-20T10:00:00Z",
  "skillCount": 12,
  "schemaVersion": 2,
  "blobShas": { "skills/find-skills": "9f3c1a2b..." }
}
```

### 3. 合并索引（`index`）

- **极简说明**：把第 1 步的 skills.sh 原始数据（`fetched-skills.jsonl`）与第 2 步扫描出的所有仓库技能（`scanned.jsonl`）按 `source` + `skillId`（从 `path` 末段推导）合并，生成最终索引 `data/index.jsonl`（以**技能**为单位平铺，每行一个完整技能记录，含 skills.sh 元信息 + 扫描得到的 `path` / `description`）。
- **对应命令**：`uv run skills-index index`

```json
// index.jsonl —— 每行一个技能
{
  "source": "vercel-labs/skills",
  "skillId": "find-skills",
  "installs": 3005209,
  "weeklyInstalls": [113781, 109199, 109085, 115475, 107969, 101120, 96861, 93130],
  "path": "skills/find-skills",
  "description": "Discover and install agent skills"
}
```

### 4. 一键更新（`update`）

- **极简说明**：把第 1~3 步串成一条命令，本地测试和 CI 统一入口。
- **对应命令**：`uv run skills-index update`
- **说明**：默认走**增量**——保留本地 `data/by-source/` 缓存（不清空），fetch 后自动清理不在本次数据中的 stale 仓库目录，随后 `scan` 复用 `pushed_at` / `blobShas` 指纹，只扫描有变化的仓库与 `SKILL.md`。`--force` 强制全量重建（先清空缓存再重扫所有仓库，用于保证一致性）；`--pages N` 只拉取 N 页 skills.sh 数据，专供冒烟测试，同样走全量路径（部分 fetch 会破坏增量缓存链条，故不启用增量）。

```bash
# 完整更新（增量：保留缓存，只扫变化的仓库/文件）
uv run skills-index update

# 强制全量重建（清空缓存后重扫所有仓库）
uv run skills-index update --force

# 本地快速冒烟测试：只拉 1 页 skills.sh 数据（全量路径，不影响缓存链）
uv run skills-index update --pages 1
```

每日数据生成即由 GitHub Actions 调用 `uv run skills-index update` 完成（见 `.github/workflows/daily.yml`）。

## 完整数据布局

```
data/
  fetched-skills.jsonl    # 第1步产物：skills.sh 原始数据汇总（中间产物）
  scanned-repos.jsonl     # 第2步产物：按仓库汇总的扫描结果（每仓库一行）
  index.jsonl             # 第3步产物：合并后的最终索引（以 skill 为单位平铺）
  by-source/
    <owner>__<repo>/      # 双下划线是 '/' 的无损替换
      fetched.jsonl       # 该仓库的 skills.sh 原始数据（source / skillId / installs / weeklyInstalls）
      scanned.jsonl       # 经 scan 发现的所有技能，含 path / description
      meta.json           # 分支 / pushedAt / skillCount / blobShas（来自 GitHub）
```

> `fetch` 只保存 skills.sh 的原始字段（`source` / `skillId` / `installs` / `weeklyInstalls`），不保存任何 URL；`scan` 只记录每个技能在仓库内的相对路径 `path` 与 `description`，也不拼出完整 URL。最终 `index.jsonl` 以技能为单位平铺，便于按 `skillId` 检索，但同样只保存 `path` 而不保存可直接访问的 `url`。需要完整 GitHub 目录链接时，只需 `source` + `path` 即可拼成 `https://github.com/<source>/tree/HEAD/<path>`（`HEAD` 恒指向仓库默认分支，无需记录分支名）。

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
# 1) 获取 skills.sh 数据，写入 data/fetched-skills.jsonl + data/by-source/（仅原始字段）
uv run skills-index fetch

# 限制页数（适合快速冒烟测试）
uv run skills-index fetch --pages 1

# 2) 扫描 GitHub 仓库里的 SKILL.md，记录每个技能的 path 与 description（跳过 pushed_at 未变化的仓库）
uv run skills-index scan

# 强制完整重新扫描
uv run skills-index scan --force

# 3) 结合前两步，生成最终索引 data/index.jsonl
uv run skills-index index

# 4) 或者一键完成以上三步
uv run skills-index update
```

## 开发

```bash
uv run ruff check .
uv run mypy src/skills_index
uv run pytest
```
