"""Enron email pipeline — command-line entry point.

    python main.py parse [--mailboxes lay-k skilling-j ...] [--limit N]
"""

import argparse
import sys
import time
from collections import Counter
from itertools import islice

from enron_pipeline.config import DEFAULT_MAILBOXES, MAILDIR, PROJECT_ROOT
from enron_pipeline.discovery import discover_emails
from enron_pipeline.extract import extract


def cmd_parse(args: argparse.Namespace) -> None:
    paths = discover_emails(MAILDIR, args.mailboxes)
    if args.limit:
        paths = islice(paths, args.limit)

    started = time.perf_counter()
    result = extract(paths, PROJECT_ROOT)
    elapsed = time.perf_counter() - started

    print(f"Mailboxes:   {', '.join(args.mailboxes)}")
    print(f"Files found: {result.total_files:,}")
    print(f"Parsed:      {len(result.emails):,}")
    print(f"Failed:      {len(result.failures):,}")
    print(f"Time:        {elapsed:.1f}s")
    if result.failures:
        print("\nFailure reasons:")
        reasons = Counter(reason.split(":")[0] for _, reason in result.failures)
        for reason, count in reasons.most_common():
            print(f"  {count:>6,}  {reason}")


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
