# 过滤机制说明

本文档完整描述 skills-index 流水线中的全部过滤规则：**仓库级过滤**（整个仓库被丢弃）、
**仓库内 skill 级过滤**（仓库内单个 SKILL.md 被丢弃）、**索引合并级过滤**（合并时技能被剔除或去重；
孤儿技能的保留策略 I1 亦在此记录）。

所有规则的实现位置均以 `源文件::函数` 标注，配置项集中在 `src/skills_index/config.py`。

---

## 总览：过滤发生在流水线的哪里

```
skills.sh API ──▶ [fetch] ──▶ [scan] ──▶ [index] ──▶ index.jsonl
                   F1 F2      S1 S2 S3    I1 I2 I3
                             F3 F4 F5
```

| 编号 | 过滤 | 位置 | 一句话规则 |
| --- | --- | --- | --- |
| F1 | 排行榜入口门槛 | `fetch` | 必须出现在 skills.sh all-time 榜单 |
| F2 | 非 GitHub 源 | `fetch::filter_github` | source 不是 `owner/repo` 形式即丢弃 |
| F3 | 仓库已不存在 | `scan::_scan_one_repo` | GitHub 404 → 删除该仓库全部缓存数据 |
| F4 | 高技能数仓库 | `scan::_scan_one_repo` | skillCount > 500（聚合型仓库）→ 丢弃并删缓存 |
| F5 | 内容指纹镜像去重 | `scan::_dedup_repos` | 整棵技能树 blob sha 全等 → 只留星数最高者 |
| S1 | 文件名约定 | `github::_parse_tarball` | 只收集 `…/SKILL.md`，其余文件一律忽略 |
| S2 | 内部路径过滤 | `config::is_internal_skill_path` | SKILL.md 位于仓库内部目录 → 丢弃 |
| S3 | 非公开 frontmatter | `github::is_nonpublic_frontmatter` | 作者声明 hidden/private/… → 丢弃 |
| I1 | 孤儿技能 | `index::run_index` | 仓库有、但 skills.sh 榜单没有 → 保留，元数据置空（`installs: 0` / `weeklyInstalls: []`） |
| I2 | 榜单失配 | `index::run_index` | 榜单有、但仓库扫描没有 → 剔除 |
| I3 | 跨仓库技能去重 | `index::_dedup_skills` | skillId + description 双匹配 → 保留 installs 最高者 |

最终索引以**仓库扫描为基准**：收录范围由扫描结果决定（扫描本身已过 F1–F5 / S1–S3 过滤），skills.sh 榜单（F1）只挂载 `installs` 等元数据，未收录技能以空元数据入索引（I1）；榜单有、仓库无的技能被 I2 剔除——索引中的每个技能都有**当前真实存在**的仓库路径背书。

---

## 一、仓库级过滤

### F1 排行榜入口门槛（隐式）

- **规则**：`fetch` 只从 skills.sh `all-time` 排行榜 API 拉取数据。不在榜单上的仓库根本不会进入流水线。
- **性质**：隐式门槛（不是显式代码分支），但是最重要的一道质量门——榜单按安装量排序，
  天然过滤了零星/无人使用的技能仓库。
- **调节方式**：无（由 skills.sh 生态决定）。

### F2 非 GitHub 源

- **规则**：`fetch.py::filter_github` 用 `config::is_github_source`（正则 `^[^/\s]+/[^/\s]+$`）
  校验 `source` 字段；非 `owner/repo` 形式（如完整 URL、其他平台来源）整条丢弃。
- **同时**：只保留 `KEEP_FIELDS` 白名单字段（`source` / `skillId` / `installs` / `weeklyInstalls`），
  其余 skills.sh 字段不落盘（**不保存任何 URL 字段**，这是项目硬约束）。
- **计数**：`dropped_non_github`。

### F3 仓库已不存在（404）

- **规则**：`scan` 阶段批量获取仓库元数据（`github::get_repo_metas`）。GitHub 返回 404
  （仓库已删除/改名/转私有）的仓库视为 definitively gone：
  - 删除其 `by-source/<owner>__<repo>/` 全部缓存数据；
  - 该仓库及其所有技能从后续索引中消失。
- **判定细节**：`github::_is_missing_repo` 沿异常链（`HttpError.__cause__`）查找 404 状态码，
  与其他失败（网络错误、5xx、限流）区分——后者只跳过本次（计为 `repos_failed`），不删数据。
- **计数**：`repos_gone`。

### F4 高技能数仓库（聚合商过滤）

- **规则**：仓库的 `skillCount`（扫描出的 SKILL.md 数量）超过 `MAX_SKILL_COUNT`（默认 **500**，
  可用 `--max-skill-count N` 覆盖，`0` 关闭）→ 整仓库丢弃并删除缓存。
- **动机**：聚合型 / awesome-list 类仓库捆绑成百上千个技能，会稀释索引质量（这类仓库的"技能"
  多为转载汇编，而非作者原生发布）。
- **覆盖两个分支**：
  - 增量分支：`pushed_at` 未变化的仓库，用缓存 `meta.json` 里的 `skillCount` 复查——
    即便此前已扫描过，规则收紧后也会被重新过滤；
  - 重扫分支：本次实际扫描出的技能数超限。
- **计数**：`repos_filtered` / `repos_filtered_high_skill`（两者相等；前者为所有仓库级丢弃的总口径，
  预留其他子类）。

### F5 内容指纹镜像去重

- **规则**：`scan.py::_dedup_repos` 在每次运行汇总前，把每个仓库的
  `{path: blob_sha}` 技能树序列化为指纹（`meta.json` 的 `blobShas` 字段，本地按 git 相同
  算法计算的内容寻址指纹）。**指纹完全一致 = 未分叉的 fork / 镜像**：组内只保留星数最高者
  （星数相同保留 skills.sh 排名靠前者），其余移出汇总并删除缓存。
- **动机**：镜像仓库的技能与原仓库逐字节相同，不去重会让同一技能在索引中出现多份。
- **为什么不用 GitHub 的 fork 标记**：已分叉的 fork（技能树已分化，如新增了自己的技能）
  指纹不同，不会被误伤；而 `fork=true` 无法区分"未分叉镜像"与"深度分叉"。
  内容指纹是行为判定，比身份标记准确。
- **边界**：`skillCount == 0` 的仓库无指纹，永不参与去重。
- **计数**：`repos_deduped`。

---

## 二、仓库内 skill 级过滤

发生在 `github.py::_parse_tarball`：下载仓库 tarball（codeload，不计入 REST API 配额）后，
逐文件做以下检查。**顺序即优先级**：S1 → S2 → S3，任一命中即丢弃该 SKILL.md（计入
`skills_filtered_nonpublic`——S2/S3 共用该计数）。

### S1 文件名约定

- **规则**：只收集路径以 `/SKILL.md` 结尾的**文件**（`rel.endswith("/SKILL.md")`），
  即技能必须位于自己的目录内（如 `skills/foo/SKILL.md`）。README、其他 md 文件一律忽略。
- **设计意图**：仓库根级单独的 `SKILL.md`（无目录前缀）**有意不收集**——skills.sh
  的安装机制面向的是目录形式的技能，根级单文件形态不构成可安装单元，不去考虑。

### S2 内部路径过滤（`config::is_internal_skill_path`）

对 SKILL.md 所在目录（去掉文件名后的 `skill_dir`）做三类检查，**匹配语义统一为：
整段精确比较、大小写不敏感**（不做子串匹配，`testing` ≠ `test`）：

**a) 状态词（任意路径段，含技能自身目录名）**

目录或技能名本身为以下词即排除——名字宣示"这不是公开技能"：

```
deprecated / hidden / private / internal / obsolete
```

（`SKILL_EXCLUDE_ANY_DIRS`。如 `skills/deprecated/foo` 或名为 `hidden` 的技能目录。）

**b) 结构词（仅中间目录段，不含最后一段）**

SKILL.md 的路径中任一**中间**目录段命中以下词即排除——测试/示例/构建产物等仓库内部结构：

```
test / tests / __tests__ / spec / e2e
example / examples / sample / samples / demo / demos
fixture / fixtures / mock / mocks / stub / stubs
template / templates / scaffold / boilerplate
doc / docs
dist / build / out / node_modules / vendor / third_party
```

（`SKILL_EXCLUDE_DIRS`。**最后一段是技能自身的目录名，豁免**：存在真实技能就叫
`test-generator` / `template` / `e2e`，不误伤。）

**c) 隐藏目录（`.` 开头）**

隐藏目录默认视为仓库配置（`.github` / `.devcontainer` / `.vscode` 等）而排除，但以下
**公开技能标准位置**豁免：

- `.claude/skills/…`、`.agents/skills/…` 等隐藏根 + `skills` 段（各 agent 工具约定的公开技能位置）；
- `.skills/…` 根本身；
- `.github` **恒不豁免**——即使形如 `.github/skills` 也是仓库配置，不算公开技能。

### S3 非公开 frontmatter（`github::is_nonpublic_frontmatter`）

解析 SKILL.md 的 YAML frontmatter，作者显式声明不对外发布的即排除：

- `public: false`；
- 以下任一字段为**真值**（`true` / `yes` / `1` 等）：
  `deprecated` / `hidden` / `private` / `internal` / `obsolete`（`HIDDEN_FRONTMATTER_MARKERS`）。

反例（均**保留**）：`hidden: false`、`public: true`、无相关字段、frontmatter 解析失败、
无 frontmatter。识别生态常见别名，不发明新标准。

---

## 三、索引合并级过滤（`index.py::run_index`）

前两步的产物在 `index` 步骤按 `(source, skillId)` 连接（`skillId` 从扫描出的 `path`
末段目录名推导）。合并以**扫描结果为基准**：所有扫描到的技能都写入 index.jsonl，
skills.sh 数据只作挂载。连接产生一个方向的失配过滤 + 一个显式去重：

### I1 孤儿技能（仓库有、榜单无）→ 保留

仓库里扫描到的 SKILL.md，其 `(source, 目录名)` 在 skills.sh 榜单数据里不存在
（未被 skills.sh 收录）→ **仍写入 index.jsonl**，`installs` 置 `0`、
`weeklyInstalls` 置 `[]`（保持记录形状统一），并追加在索引末尾——有榜单数据的
技能按 skills.sh 排名顺序在前。
计数：`scan_only`。

### I2 榜单失配（榜单有、仓库无）

skills.sh 榜单收录的技能，在对应仓库的扫描产物里找不到（作者已删除、改名、移走）→ 剔除。
这保证索引中的每个技能都有**当前真实存在**的仓库路径背书。
计数：`not_in_repo`。

### I3 跨仓库技能去重（`index.py::_dedup_skills`）

- **规则**：`skillId` **且** `description`（来自 frontmatter，非空）完全一致的记录视为
  同一技能的镜像/拷贝 → 组内只保留 `installs` 更高者（相同则保留 skills.sh 排名靠前者）。
- **为什么必须双匹配**：`skillId` 只是技能目录名，**非全局唯一**——数据中真实存在同名不同
  实现的技能（如 `ai-video-generation` 在两个仓库分别是 RunComfy 与 inference.sh 的不同
  实现，路径/描述/安装量均不同）。叠加 `description`（来自仓库内容）才能证明"是同一个技能"。
- **保守边界**：`description` 为空不参与去重——**未知 ≠ 相同**。
- **与 F5 的关系**：F5 兜住整仓库镜像（指纹全等）；I3 兜住部分拷贝（仓库只复制了几个技能、
  其余自研，指纹不等）。
- **计数**：`deduped_skills`。

---

## 四、设计原则

1. **以扫描为基准 + 单一存在性门**：索引收录范围由仓库扫描决定（扫描本身已过
   F1–F5 / S1–S3 过滤）；skills.sh 榜单（F1）只提供 `installs` 等元数据，未收录的
   技能以空元数据入索引（I1）。I2 是唯一的存在性门——索引中的每个技能都有
   **当前真实存在**的仓库路径背书。
2. **内容寻址，不信任身份标记**：去重指纹用本地计算的 git blob sha（与 GitHub 一致），
   不用 `fork=true` 这类身份标记——身份会说谎（已分叉的 fork），内容不会。
3. **精确匹配，防止误伤**：目录过滤是整段精确比较而非子串（`testing` ≠ `test`）；
   技能自身目录名豁免结构词（真有技能叫 `template`）；`description` 为空不去重。
4. **过滤即清理**：所有仓库级丢弃（F3/F4/F5）都会**删除对应的 `by-source/` 缓存**，
   保证后续 `index` 步骤自然收不到它们的技能，无需二次过滤。

## 五、配置项（`src/skills_index/config.py`）

| 常量 | 默认值 | 作用 |
| --- | --- | --- |
| `MAX_SKILL_COUNT` | `500` | F4 上限；`--max-skill-count N` 覆盖，`0` 关闭 |
| `SKILL_EXCLUDE_DIRS` | 结构词集合 | S2-b 中间目录段排除词 |
| `SKILL_EXCLUDE_ANY_DIRS` | 状态词集合 | S2-a 任意段排除词（含技能名） |
| `HIDDEN_FRONTMATTER_MARKERS` | 状态词元组 | S3 frontmatter 非公开标记 |
| `SCHEMA_VERSION` | `4` | 过滤规则变更时递增 → 触发存量缓存一次性全量重扫 |

> 修改任何过滤规则后应递增 `SCHEMA_VERSION`：增量模式下旧缓存按旧规则生成，
  只有版本号变化才会强制重建（`scan.py::plan_blob_fetches` / `merge_skill_records`）。

## 六、观测：run-summary 计数对照

每次 `update` 运行后 `data/run-summary.md` 中的字段与上述规则的对应：

| run-summary 字段 | 对应过滤 |
| --- | --- |
| `dropped_non_github` | F2 |
| `repos_gone` | F3 |
| `repos_filtered` / `repos_filtered_high_skill` | F4 |
| `repos_deduped` | F5（命中时才显示） |
| `skills_filtered_nonpublic` | S2 + S3（每次实际解析 tarball 的量，增量跳过的仓库不计） |
| `scan_only` | I1（保留计数，非过滤量） |
| `not_in_repo` | I2 |
| `deduped_skills` | I3（命中时才显示） |

Scan 汇总行还有 breakdown check：`skipped + updated + failed + gone + filtered == repos_total`
（✓/⚠），用于校验仓库级过滤没有漏计。注意 `repos_deduped` 是 `skipped`/`updated` 的
事后细分（去重发生在扫描完成之后），不参与该恒等式。
