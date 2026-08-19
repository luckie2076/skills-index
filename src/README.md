# `skills_index` — 源码模块结构

本目录（`src/`）存放 `skills_index` 包的源码树，采用标准的 **src 布局**（src-layout），
通过 [hatchling](https://hatch.pypa.io/) 构建：

```
src/
└── skills_index/        # 可导入的包（包名 == 目录名 == 入口点）
    ├── __init__.py      # 包版本号
    ├── cli.py           # argparse 入口（skills-index fetch | scan | index）
    ├── config.py        # 常量、路径、共享类型、token、source<->dir 映射
    ├── http.py          # 轻量 httpx 封装：客户端、重试、GitHub 鉴权
    ├── io_utils.py      # JSON / JSONL 读写辅助函数
    ├── github.py        # GitHub API 接口：分支、树、skill 目录
    ├── fetch.py         # 流水线：拉取 skills.sh -> 过滤 -> 持久化（不含 URL 解析）
    ├── scan.py          # 按 pushed_at 增量扫描 GitHub 仓库
    └── index.py         # 合并 fetch + scan 产物，生成 data/index.jsonl
└── README.md            # 本文件
```

外层 `src/` 是一个**隔离边界**：只有 `skills_index/` 会被打包，因此项目元数据
（`pyproject.toml`、`README.md`、`tests/`、`data/`）不会泄漏到 wheel 中。`skills_index/`
才是真正的包，其名称与 `pyproject.toml` 中的 `packages = ["skills_index"]` 以及
`skills-index = "skills_index.cli:main"` 配置项一致（即 `packages = ["src/skills_index"]`
和 `skills-index = "skills_index.cli:main"`）。

## 模块职责

| 模块       | 职责                                                                                                                 |
| ---------- | -------------------------------------------------------------------------------------------------------------------- |
| `__init__` | 暴露 `__version__`。                                                                                                 |
| `cli`      | 解析 argv，提供 `fetch` / `scan` / `index` 子命令，调用流水线。                                                    |
| `config`   | 路径、外部端点、`Skill` `TypedDict`、GitHub token 发现，以及无损的 `owner/repo` <-> `owner__repo` 映射的唯一来源。   |
| `http`     | 封装好的 `httpx.Client` 工厂 + 带重试和限流友好提示的 `get_json`。不手写 HTTP。                                      |
| `io_utils` | `write_jsonl` / `read_jsonl` / `write_json` / `read_json` 持久化辅助函数。                                           |
| `github`   | GitHub 元数据/树查询。使用运行期作用域的 `@cache` 对仓库元请求和 skill 树遍历去重。                                                             |
| `fetch`    | 从 skills.sh 拉取、GitHub 来源过滤，并写入 `data/skills-sh-all.jsonl` + `data/by-source/`（仅原始字段，不解析 GitHub URL）。 |
| `scan`     | 遍历 `data/by-source/`，通过 `pushed_at` 跳过未变更的仓库，扫描出每个仓库内的所有 `SKILL.md` 技能（含 `url` / `path`），输出各仓库的 `skills-github.jsonl` + `github-meta.json`，并汇总生成 `data/scan-all.jsonl`（以仓库为单位、一行一个仓库，含 `branch` / `pushedAt` / `skillCount` / `skills[]`，其中 `skills[]` 为纯 path 字符串数组）。 |
| `index`    | 读取 `data/skills-sh-all.jsonl` 与各 `skills-github.jsonl`，按 `source`+`skillId` 合并，生成最终 `data/index.jsonl`（以 skill 为单位平铺，每行一个完整技能记录）。 |

## 依赖方向（无环）

```
cli ──▶ fetch ──┐
       scan  ───┴─▶ github ──▶ http
         │  │            │         │
         └──┴──── io_utils ◀───────┘
                  ▲
                  └──────────── config  （被所有模块依赖；自身不依赖任何模块）

index ──▶ fetch / scan 产物（读 skills-sh-all.jsonl + 各 skills-github.jsonl，写 index.jsonl）
```

- `config` 不依赖任何业务模块——它是共享的叶子节点。
- `http` 仅依赖 `config`。
- `github`、`fetch`、`scan` 依赖 `http` / `config` / `io_utils`。
- `cli` 只负责把 `fetch` 和 `scan` 串联起来。

无循环：每条箭头都指向内部的 `config`。

## 模块划分评估

**是否合理且必要？** 是。每个模块都有单一、清晰的职责，且名称与之匹配。拆分遵循问题本身的自然分界
（CLI vs. 流水线 vs. 外部 API vs. 传输层 vs. 持久化），因此该划分既有正当性又保持精简——不存在过度设计。

**是否最优？** 对于此规模的项目，大体上是的。几点说明：

- `config.py` 把常量、路径、`Skill` 类型、token 发现以及 source/dir 映射打包在一起。对于小项目这是可以接受的；
  进一步拆分只会增加仪式感而无实际收益。
- `fetch.py` 把拉取 + 过滤 + 补全 + 分发打包在一起。它们同属一条流水线的不同阶段，放在一起可读性良好；
  过早地把每个阶段抽离出来反而不妥。
- 本次已应用的小幅质量改进：
  - `io_utils` 现使用顶层的 `import json` 取代 `__import__("json")`，并新增 `read_json(path, default=None)` 辅助函数。
  - `github.py` 统一使用 `@cache` 进行缓存（此前混用了 `@cache` 与手动的 `_branch_cache` / `_tree_cache`），
    并通过单一缓存的 `_repo_info` 对仓库元请求去重。
  - `scan.py` 复用 `io_utils.read_json` / `write_json` 读写仓库元数据与汇总产物，而非重新实现 JSON 的读写。
