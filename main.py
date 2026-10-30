"""Enron email pipeline — command-line entry point.

    python main.py parse [--mailboxes lay-k skilling-j ...] [--limit N]
"""

import argparse
import sys
import time
from itertools import islice

from enron_pipeline.config import (
    DEFAULT_MAILBOXES, ERROR_LOG, EXTRACTION_STATS, MAILDIR, PROJECT_ROOT,
)
from enron_pipeline.discovery import discover_emails
from enron_pipeline.extract import extract
from enron_pipeline.stats import build_stats, format_stats, write_error_log, write_stats


def cmd_parse(args: argparse.Namespace) -> None:
    paths = discover_emails(MAILDIR, args.mailboxes)
    if args.limit:
        paths = islice(paths, args.limit)

    started = time.perf_counter()
    result = extract(paths, PROJECT_ROOT)
    elapsed = time.perf_counter() - started

    stats = build_stats(result, args.mailboxes)
    write_stats(stats, EXTRACTION_STATS)
    write_error_log(result.failures, ERROR_LOG)

    print(format_stats(stats))
    print(f"\nParsed in {elapsed:.1f}s")
    print(f"Error log:  {ERROR_LOG.relative_to(PROJECT_ROOT)}")
    print(f"Stats JSON: {EXTRACTION_STATS.relative_to(PROJECT_ROOT)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enron email extraction pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("parse", help="parse emails and print extraction statistics")
    p.add_argument("--mailboxes", nargs="+", default=list(DEFAULT_MAILBOXES))
    p.add_argument("--limit", type=int, help="only parse the first N files (for quick runs)")
    p.set_defaults(func=cmd_parse)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
