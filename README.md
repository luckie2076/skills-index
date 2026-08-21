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

每个 Release 的 tag 形如 `data-YYYYMMDDThhmmssZ`（UTC 时间戳，由每日 CI 自动生成，例如 `data-20260820T143015Z`）。**生产环境应锁定到固定 tag**，而不是 `latest`：

- **可预测 / 可复现**：固定 tag 指向某一确定快照，下游缓存失效或被清理后重新拉取，拿到的仍是同一份内容；便于回溯"当时用的到底是哪份索引"。
- **缓存友好**：`releases/download/<tag>/<file>` 是稳定直链，CDN / 代理可长期缓存、命中率高；而 `releases/latest/download/...` 是 302 重定向到最新 tag，重定向目标随时变化，缓存 key 跟着变、命中率下降，且返回通常不被视为可缓存。

```bash
# 生产环境：固定 tag（推荐，可预测 + 缓存友好）
# tag 格式：data-YYYYMMDDThhmmssZ，从 Releases 页面或 gh CLI 获取
TAG=data-20260820T143015Z
curl -L -o index.jsonl \
  https://github.com/luckie2076/skills-index/releases/download/$TAG/index.jsonl

# 拉取完整数据快照（同样用固定 tag，保证与 index.jsonl 同源）
curl -L -o data.tar.gz \
  https://github.com/luckie2076/skills-index/releases/download/$TAG/data.tar.gz
```

> 想拿到**最新**那份快照的 tag 而不写死时间戳，可用 `gh release list` 解析最新的 `data-` Release（注意仍是固定 tag，不是 302 的 `latest`）：
>
> ```bash
> TAG=$(gh release list --limit 100 --json tagName,createdAt \
>   --jq 'sort_by(.createdAt) | reverse | .[].tagName' \
>   | grep '^data-' | head -n 1)
> curl -L -o index.jsonl \
>   https://github.com/luckie2076/skills-index/releases/download/$TAG/index.jsonl
> ```
>
> 若要**锁定到某一天**的快照（可复现），把上面的 `TAG` 直接写死成当天的 tag 即可，例如 `TAG=data-20260820T143015Z`。

> 固定 tag 与 `latest` 总是同源（同一轮 CI 产物）；用固定 tag 可避免并发/重试时拉到跨 Release 的 `index.jsonl` + `data.tar.gz` 组合。

```bash
# 本地调试 / 想要最新：用 latest（注意 302 重定向、缓存不友好）
curl -L -o index.jsonl \
  https://github.com/luckie2076/skills-index/releases/latest/download/index.jsonl
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

- **极简说明**：按 `pushed_at` 增量扫描各 GitHub 仓库，找出其中真正的 `SKILL.md` 技能定义（跳过未变更的仓库）。增量粒度是**文件级 blob sha**：`pushed_at` 变化的仓库，下载其**代码压缩包（codeload tarball，不计入 REST API 速率配额）**，本地解压遍历所有 `SKILL.md`，用 git 相同算法在本地计算每个文件的 blob sha（与 GitHub 的 blob sha 一致），只对 sha 相比上次发生变化的 `SKILL.md` 重新解析 YAML frontmatter 提取 `description`，未变化的技能直接复用本地缓存；从仓库中消失的技能会被自动移除。每个技能只记录仓库内相对路径 `path`，完整 GitHub 目录 URL 由调用方用 `source` + `path` 拼接。**仓库本身已不存在时（GitHub 返回 404，如已删除/改名/转为私有）也不会记录该仓库**：会删除其残留的旧扫描数据，该仓库及其技能随之从后续索引中移除，不再出现。仓库级信息会记录其 **star 数**（来自同一仓库元数据请求的 `stargazers_count`，随 `pushed_at` 一起获取，不增加额外请求）。
- **对应命令**：`uv run skills-index scan`（加 `--force` 可强制全量重扫；扫描产物格式升级时会自动触发一次性全量重扫）
- **按 star 数过滤**：加 `--min-stars N` 可跳过 star 数小于 `N` 的仓库（默认 `0` = 不限制）。被过滤的仓库会**删除其残留的旧扫描数据**（与该仓库 404 时同样处理），因此其技能不会漏进后续索引——即便该仓库此前已扫描过、走的是「未变更」分支也会重新被过滤。star 数取自同一元数据请求的 `stargazers_count`，不增加额外请求。
- **产物形状**：在每个仓库目录下输出 `scanned.jsonl`（扫描发现的所有技能，含 `path` / `description`）与 `meta.json`（仓库元信息，含 `branch` / `pushedAt` / `stars` / `blobShas` 文件级增量指纹与 `schemaVersion`）。汇总产物 `scanned-repos.jsonl` 每仓库一行，同样含 `stars`。

```json
// scanned.jsonl 中的一行
{ "path": "skills/find-skills", "description": "Discover and install agent skills" }
```

```json
// meta.json
{
  "branch": "main",
  "pushedAt": "2026-08-10T12:00:00Z",
  "stars": 1284,
  "lastScanned": "2026-08-20T10:00:00Z",
  "skillCount": 12,
  "schemaVersion": 3,
  "blobShas": { "skills/find-skills": "9f3c1a2b..." }
}
```

### 3. 合并索引（`index`）

- **极简说明**：把第 1 步的 skills.sh 原始数据（`fetched-skills.jsonl`）与第 2 步扫描出的所有仓库技能（`scanned.jsonl`）按 `source` + `skillId`（从 `path` 末段推导）合并，生成最终索引 `data/index.jsonl`（以**技能**为单位平铺，每行一个完整技能记录，含 skills.sh 元信息 + 扫描得到的 `path` / `description`）。**只有同时存在于 skills.sh 与仓库扫描中的技能才会进入索引**：仅出现在 skills.sh、但仓库中已不存在（未被扫描到）的技能会被剔除，不会记录；同理，仓库已不存在（第 2 步 404 时已清理其数据）的仓库，其所有技能也不会进入索引。
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
- **说明**：默认走**增量**——保留本地 `data/by-source/` 缓存（不清空），fetch 后自动清理不在本次数据中的 stale 仓库目录，随后 `scan` 复用 `pushed_at` / `blobShas` 指纹，只扫描有变化的仓库与 `SKILL.md`。`--force` 强制全量重建（先清空缓存再重扫所有仓库，用于保证一致性）；`--pages N` 只拉取 N 页 skills.sh 数据，专供冒烟测试，同样走全量路径（部分 fetch 会破坏增量缓存链条，故不启用增量）；`--min-stars N` 把 star 数小于 `N` 的仓库排除出索引（见第 2 步）。
- **线上部署**：CI（`.github/workflows/daily.yml`）在 `main` 与 `test` 分支均用 `--min-stars 100` 运行 `update`，因此发布的 `index.jsonl`（与 `alpha-` 预发布）只收录 star 数 ≥ 100 的仓库。`main` 走全量 fetch，`test` 仅拉 1 页用于冒烟。

```bash
# 完整更新（增量：保留缓存，只扫变化的仓库/文件）
uv run skills-index update

# 强制全量重建（清空缓存后重扫所有仓库）
uv run skills-index update --force

# 排除 star 数 < 100 的仓库（过滤同样适用于此前已扫描过的缓存仓库）
uv run skills-index update --min-stars 100

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
      meta.json           # 分支 / pushedAt / stars / skillCount / blobShas（来自 GitHub）
```

> `fetch` 只保存 skills.sh 的原始字段（`source` / `skillId` / `installs` / `weeklyInstalls`），不保存任何 URL；`scan` 只记录每个技能在仓库内的相对路径 `path` 与 `description`，也不拼出完整 URL。最终 `index.jsonl` 以技能为单位平铺，便于按 `skillId` 检索，但同样只保存 `path` 而不保存可直接访问的 `url`。合并时只保留**同时存在于 skills.sh 与仓库扫描中**的技能（skills.sh 有、但仓库中已不存在的技能不会记录）。需要完整 GitHub 目录链接时，只需 `source` + `path` 即可拼成 `https://github.com/<source>/tree/HEAD/<path>`（`HEAD` 恒指向仓库默认分支，无需记录分支名）。

## 环境要求

- Python >= 3.11
- [`uv`](https://docs.astral.sh/uv/)

可选：设置 `GITHUB_TOKEN`（环境变量或 `.env` 文件）可消除未认证的 60 次/小时限制。注意配额差异：GitHub Actions 内置 `GITHUB_TOKEN` 为 **1000 次/小时/仓库**；推荐在仓库 Secrets 里配置 `GH_PAT`（个人访问令牌，**5000 次/小时**），代码会优先使用它。`SKILL.md` 内容通过 codeload tarball 获取，不计入以上配额。

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
