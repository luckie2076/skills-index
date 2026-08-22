"""Command-line entry point for skills-index."""

from __future__ import annotations

import argparse
import shutil
import sys
import time

from .config import (
    BY_SOURCE_DIR,
    DATA_DIR,
    FETCHED_SKILLS,
    INDEX_JSONL,
    JSON,
    MAX_SKILL_COUNT,
    SCANNED_REPOS,
    SCANNED_REPOS_BY_SKILLCOUNT,
    SCANNED_REPOS_BY_STARS,
)
from .fetch import prune_stale_repos, run_fetch
from .index import run_index
from .scan import scan_repositories


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="skills-index",
        description="Aggregate skills.sh metadata and GitHub skill locations.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    fetch_p = sub.add_parser("fetch", help="fetch skills.sh data (no GitHub URL resolution)")
    fetch_p.add_argument("--pages", type=int, default=0, help="max pages (0 = all)")

    scan_p = sub.add_parser(
        "scan", help="scan GitHub repos in data/by-source (incremental)"
    )
    scan_p.add_argument(
        "--force", action="store_true", help="ignore cached pushed_at and rescan all"
    )
    scan_p.add_argument(
        "--max-skill-count",
        type=int,
        default=None,
        help="skip repos with more than this many skills "
             f"(default: config.MAX_SKILL_COUNT={MAX_SKILL_COUNT})",
    )

    sub.add_parser(
        "index", help="merge fetched + scanned data into data/index.jsonl"
    )

    update_p = sub.add_parser(
        "update",
        help="run fetch -> scan -> index in sequence (one-shot pipeline)",
    )
    update_p.add_argument(
        "--pages", type=int, default=0, help="max fetch pages (0 = all)"
    )
    update_p.add_argument(
        "--force", action="store_true", help="force a full rescan in scan"
    )
    update_p.add_argument(
        "--max-skill-count",
        type=int,
        default=None,
        help="skip repos with more than this many skills "
             f"(default: config.MAX_SKILL_COUNT={MAX_SKILL_COUNT})",
    )

    return p


def _build_summary(
    fetch_sum: dict[str, JSON],
    scan_sum: dict[str, JSON],
    index_sum: dict[str, JSON],
    *,
    total: float,
    fetch: float,
    scan: float,
    index: float,
    pages: int,
) -> str:
    """Render a Markdown run report shown in the Release body and saved to disk."""
    pages_str = "all" if not pages else str(pages)
    scope_str = "full refresh" if not pages else f"smoke test ({pages} page)"
    failed = fetch_sum.get("failed_pages") or []
    lines = [
        "## Run summary",
        "",
        f"- **Scope:** {scope_str}",
        f"- **Total time:** {total:.1f}s "
        f"(fetch {fetch:.1f}s / scan {scan:.1f}s / index {index:.1f}s)",
        "",
        "### Fetch (skills.sh)",
        f"- Pages fetched: `{pages_str}`",
        f"- Skills kept: `{fetch_sum.get('kept_github', 0)}` "
        f"of `{fetch_sum.get('raw_skills', 0)}` raw "
        f"(`{fetch_sum.get('dropped_non_github', 0)}` non-GitHub dropped)",
        f"- Source repos: `{fetch_sum.get('source_dirs', 0)}`",
    ]
    # pruned_stale 仅在增量模式注入;非增量(--pages/--force)不显示
    if fetch_sum.get("pruned_stale") is not None:
        lines.append(
            f"- Pruned stale repo dirs: `{fetch_sum.get('pruned_stale', 0)}`"
        )
    if failed:
        lines.append(f"- Skipped pages (errors): `{len(failed)}` {failed}")

    repos_total = scan_sum.get("repos_total", 0)
    bd_skipped = scan_sum.get("repos_skipped", 0)
    bd_updated = scan_sum.get("repos_updated", 0)
    bd_failed = scan_sum.get("repos_failed", 0)
    bd_gone = scan_sum.get("repos_gone", 0)
    bd_filtered = scan_sum.get("repos_filtered", 0)
    bd_tree = scan_sum.get("repos_tree_skipped", 0)
    bd_sum = bd_skipped + bd_updated + bd_failed + bd_gone + bd_filtered + bd_tree
    bd_check = "✓ matches total" if bd_sum == repos_total else "⚠ MISMATCH vs total"
    share = (
        f"{bd_updated}/{repos_total} ({bd_updated / repos_total * 100:.0f}%)"
        if repos_total
        else f"{bd_updated}/{repos_total} (n/a)"
    )
    lines += [
        "",
        "### Scan (GitHub repos)",
        f"- Repos total: `{repos_total}`",
        f"- Skipped (unchanged): `{bd_skipped}`",
        f"- Tree-skipped (push w/o skill changes): `{bd_tree}`",
        f"- Updated (incremental): `{bd_updated}`",
        f"- Failed: `{bd_failed}`",
        f"- Removed (repo not found): `{bd_gone}`",
        f"- Filtered (high-skill > {MAX_SKILL_COUNT}): `{bd_filtered}`",
        f"- Breakdown check: `{bd_sum}` {bd_check}; updated share {share}",
        f"- Skills scanned (all valid repos): `{scan_sum.get('skills_scanned', 0)}`",
        f"- Skills scanned this run (incremental): `{scan_sum.get('skills_scanned_new', 0)}`",
        f"- Skills filtered non-public (this run): "
        f"`{scan_sum.get('skills_filtered_nonpublic', 0)}`",
    ]
    # 去重行仅在命中时显示（去重仓库已计入 skipped/updated，属信息性细分）
    if scan_sum.get("repos_deduped"):
        lines.append(
            f"- Deduped (identical skill tree, kept best-starred): "
            f"`{scan_sum['repos_deduped']}`"
        )
    lines += [
        "",
        "### Index (merged)",
        f"- Scanned merged: `{index_sum.get('scanned_merged', 0)}`",
        f"- Orphans skipped: `{index_sum.get('orphans', 0)}`",
        f"- Not in repo (dropped): `{index_sum.get('not_in_repo', 0)}`",
    ]
    if index_sum.get("deduped_skills"):
        lines.append(
            f"- Cross-repo duplicates dropped: `{index_sum['deduped_skills']}`"
        )
    lines += [
        f"- **Final index entries: `{index_sum.get('index', 0)}`**",
        "",
        "### Artifacts",
        "- `data.tar.gz` — full `data/` tree",
        "- `index.jsonl` — merged skills index",
        "- `fetched-skills.jsonl` — raw skills.sh data",
        "- `scanned-repos.jsonl` — per-repo scan summary (scan order)",
        "- `scanned-repos-by-stars.jsonl` — per-repo scan summary (sorted by stars)",
        "- `scanned-repos-by-skillcount.jsonl` — per-repo scan summary (sorted by skillCount)",
    ]
    return "\n".join(lines) + "\n"


def clean_workspace() -> None:
    """Remove previous run artifacts so a one-shot `update` rebuilds from zero.

    `update` is a from-scratch pipeline: fetch -> scan -> index. Leftover files
    from an earlier run (e.g. a stale full scan on a machine that also ran a
    single-page test) would otherwise leak into `index.jsonl`, making the
    published artifacts inconsistent with the fetched data. We wipe the root
    summaries and every per-source intermediate file, but keep the directory
    tree so fresh runs reconstruct it.
    """
    for root_file in (
        FETCHED_SKILLS,
        INDEX_JSONL,
        SCANNED_REPOS,
        SCANNED_REPOS_BY_STARS,
        SCANNED_REPOS_BY_SKILLCOUNT,
    ):
        if root_file.exists():
            root_file.unlink()
            print(f"[clean] removed {root_file.name}")

    # Wipe the entire per-source tree so `scan` only ever processes repos that
    # `fetch` just wrote. Keeping stale dirs (with cached meta.json) would let
    # their scanned skills leak into index.jsonl, desyncing it from the fetched
    # data. `update` is a from-scratch pipeline; incremental reuse is opt-in via
    # the separate `fetch`/`scan`/`index` commands.
    if BY_SOURCE_DIR.exists():
        for repo_dir in BY_SOURCE_DIR.iterdir():
            if repo_dir.is_dir():
                shutil.rmtree(repo_dir)
        print(f"[clean] wiped per-source tree under {BY_SOURCE_DIR.name}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "fetch":
        run_fetch(max_pages=args.pages)
        return 0

    if args.command == "scan":
        scan_repositories(
            force=args.force,
            max_skill_count=args.max_skill_count,
        )
        return 0

    if args.command == "index":
        run_index()
        return 0

    if args.command == "update":
        t_start = time.monotonic()
        # Incremental (default): keep the on-disk by-source cache so `scan` can
        # reuse pushed_at / blob sha fingerprints. A partial fetch (`--pages N`,
        # smoke tests) or `--force` falls back to the clean full-build path: a
        # partial fetch would otherwise prune most cached repos and break the
        # incremental chain, and `--force` promises a from-scratch rebuild.
        incremental = args.pages == 0 and not args.force
        if not incremental:
            clean_workspace()
        skills, fetch_sum = run_fetch(max_pages=args.pages)
        if incremental:
            # Drop by-source dirs whose repo vanished from this fetch so their
            # stale scanned.jsonl cannot leak into index.jsonl.
            sources = {str(s.get("source", "")).strip() for s in skills}
            fetch_sum["pruned_stale"] = prune_stale_repos(sources)
            print(
                f"  [prune] removed {fetch_sum['pruned_stale']} stale repo dir(s)"
            )
        t_after_fetch = time.monotonic()
        scan_sum = scan_repositories(
            force=args.force,
            max_skill_count=args.max_skill_count,
        )
        t_after_scan = time.monotonic()
        _, index_sum = run_index()
        t_after_index = time.monotonic()

        t_fetch = t_after_fetch - t_start
        t_scan = t_after_scan - t_after_fetch
        t_index = t_after_index - t_after_scan
        t_total = t_after_index - t_start
        print(
            f"[timer] total={t_total:.1f}s "
            f"fetch={t_fetch:.1f}s scan={t_scan:.1f}s index={t_index:.1f}s"
        )
        summary = _build_summary(
            fetch_sum, scan_sum, index_sum,
            total=t_total, fetch=t_fetch, scan=t_scan, index=t_index,
            pages=args.pages,
        )
        (DATA_DIR / "run-summary.md").write_text(summary)
        print("wrote data/run-summary.md")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
