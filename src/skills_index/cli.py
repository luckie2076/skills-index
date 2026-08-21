"""Command-line entry point for skills-index."""

from __future__ import annotations

import argparse
import shutil
import sys
import time

from .config import BY_SOURCE_DIR, DATA_DIR, FETCHED_SKILLS, INDEX_JSONL, SCANNED_REPOS
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
        "--min-stars",
        type=int,
        default=0,
        help="skip repos with fewer than this many stars (0 = no limit)",
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
        "--min-stars",
        type=int,
        default=0,
        help="skip repos with fewer than this many stars (0 = no limit)",
    )

    return p


def _build_summary(
    fetch_sum: dict,
    scan_sum: dict,
    index_sum: dict,
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
        f"- **Total time:** {total:.1f}s (fetch {fetch:.1f}s / scan {scan:.1f}s / index {index:.1f}s)",
        "",
        "### Fetch (skills.sh)",
        f"- Pages fetched: `{pages_str}`",
        f"- Raw skills: `{fetch_sum.get('raw_skills', 0)}`",
        f"- Kept (GitHub sources): `{fetch_sum.get('kept_github', 0)}`",
        f"- Dropped (non-GitHub): `{fetch_sum.get('dropped_non_github', 0)}`",
        f"- Source repos: `{fetch_sum.get('source_dirs', 0)}`",
        f"- Pruned stale repo dirs: `{fetch_sum.get('pruned_stale', 0)}`",
    ]
    if failed:
        lines.append(f"- Skipped pages (errors): `{len(failed)}` {failed}")
    lines += [
        "",
        "### Scan (GitHub repos)",
        f"- Repos total: `{scan_sum.get('repos_total', 0)}`",
        f"- Skipped (unchanged): `{scan_sum.get('repos_skipped', 0)}`",
        f"- Updated (incremental): `{scan_sum.get('repos_updated', 0)}`",
        f"- Failed: `{scan_sum.get('repos_failed', 0)}`",
        f"- Removed (repo not found): `{scan_sum.get('repos_gone', 0)}`",
        f"- Filtered (low stars): `{scan_sum.get('repos_filtered', 0)}`",
        f"- Skills scanned: `{scan_sum.get('skills_scanned', 0)}`",
        "",
        "### Index (merged)",
        f"- Fetched skills: `{index_sum.get('fetched', 0)}`",
        f"- Scanned merged: `{index_sum.get('scanned_merged', 0)}`",
        f"- Orphans skipped: `{index_sum.get('orphans', 0)}`",
        f"- Not in repo (dropped): `{index_sum.get('not_in_repo', 0)}`",
        f"- **Final index entries: `{index_sum.get('index', 0)}`**",
        "",
        "### Artifacts",
        "- `data.tar.gz` — full `data/` tree",
        "- `index.jsonl` — merged skills index",
        "- `fetched-skills.jsonl` — raw skills.sh data",
        "- `scanned-repos.jsonl` — per-repo scan summary",
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
    for root_file in (FETCHED_SKILLS, INDEX_JSONL, SCANNED_REPOS):
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
        scan_repositories(force=args.force, min_stars=args.min_stars)
        return 0

    if args.command == "index":
        run_index()
        return 0

    if args.command == "update":
        t0 = time.monotonic()
        # Incremental (default): keep the on-disk by-source cache so `scan` can
        # reuse pushed_at / blob sha fingerprints. A partial fetch (`--pages N`,
        # smoke tests) or `--force` falls back to the clean full-build path: a
        # partial fetch would otherwise prune most cached repos and break the
        # incremental chain, and `--force` promises a from-scratch rebuild.
        incremental = args.pages == 0 and not args.force
        if not incremental:
            clean_workspace()
        skills, fetch_sum = run_fetch(max_pages=args.pages)
        t_fetch = time.monotonic() - t0
        if incremental:
            # Drop by-source dirs whose repo vanished from this fetch so their
            # stale scanned.jsonl cannot leak into index.jsonl.
            sources = {str(s.get("source", "")).strip() for s in skills}
            fetch_sum["pruned_stale"] = prune_stale_repos(sources)
            print(
                f"  [prune] removed {fetch_sum['pruned_stale']} stale repo dir(s)"
            )
        scan_sum = scan_repositories(force=args.force, min_stars=args.min_stars)
        t_scan = time.monotonic() - t_fetch - t0
        _, index_sum = run_index()
        t_index = time.monotonic() - t_scan - t_fetch - t0
        t_total = time.monotonic() - t0
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
