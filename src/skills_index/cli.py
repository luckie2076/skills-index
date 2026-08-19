"""Command-line entry point for skills-index."""

from __future__ import annotations

import argparse
import sys

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
        "index", help="merge fetched + scanned data into data/index.jsonl + data/index-byrepo.jsonl"
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

    return 2


if __name__ == "__main__":
    sys.exit(main())
