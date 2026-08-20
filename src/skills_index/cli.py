"""Command-line entry point for skills-index."""

from __future__ import annotations

import argparse
import sys
import time

from .fetch import run_fetch
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

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "fetch":
        run_fetch(max_pages=args.pages)
        return 0

    if args.command == "scan":
        scan_repositories(force=args.force)
        return 0

    if args.command == "index":
        run_index()
        return 0

    if args.command == "update":
        t0 = time.monotonic()
        run_fetch(max_pages=args.pages)
        t_fetch = time.monotonic() - t0
        scan_repositories(force=args.force)
        t_scan = time.monotonic() - t_fetch - t0
        run_index()
        t_index = time.monotonic() - t_scan - t_fetch - t0
        t_total = time.monotonic() - t0
        print(
            f"[timer] total={t_total:.1f}s "
            f"fetch={t_fetch:.1f}s scan={t_scan:.1f}s index={t_index:.1f}s"
        )
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
