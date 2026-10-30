"""Enron email pipeline — command-line entry point.

    python main.py parse   [--mailboxes ...] [--limit N]   # extraction stats only, no DB
    python main.py ingest  [--mailboxes ...] [--reset]     # parse + load into SQLite
"""

import argparse
import sys
import time
from itertools import islice
from pathlib import Path

from enron_pipeline import db
from enron_pipeline.config import (
    DB_PATH, DEFAULT_MAILBOXES, ERROR_LOG, EXTRACTION_STATS, MAILDIR, PROJECT_ROOT, SCHEMA_PATH,
)
from enron_pipeline.discovery import discover_emails
from enron_pipeline.extract import ExtractionResult, extract
from enron_pipeline.stats import build_stats, format_stats, write_error_log, write_stats


def run_extraction(args: argparse.Namespace) -> ExtractionResult:
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
    print(f"Error log:  {_display(ERROR_LOG)}")
    print(f"Stats JSON: {_display(EXTRACTION_STATS)}")
    return result


def cmd_parse(args: argparse.Namespace) -> None:
    run_extraction(args)


def cmd_ingest(args: argparse.Namespace) -> None:
    result = run_extraction(args)

    if args.reset and args.db.exists():
        args.db.unlink()
    conn = db.connect(args.db)
    try:
        db.init_schema(conn, SCHEMA_PATH)
        loaded = db.load_emails(conn, result.emails)
        total = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    finally:
        conn.close()

    print(f"\nDatabase:   {_display(args.db)}")
    print(f"Inserted:   {loaded.inserted:,}")
    print(f"Skipped:    {loaded.skipped:,}  (already in database)")
    print(f"Total rows: {total:,}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enron email extraction pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    extraction = argparse.ArgumentParser(add_help=False)
    extraction.add_argument("--mailboxes", nargs="+", default=list(DEFAULT_MAILBOXES))
    extraction.add_argument("--limit", type=int, help="only parse the first N files (for quick runs)")

    database = argparse.ArgumentParser(add_help=False)
    database.add_argument("--db", type=_path, default=DB_PATH, help="SQLite database file")

    p = sub.add_parser("parse", parents=[extraction], help="parse emails and print extraction statistics")
    p.set_defaults(func=cmd_parse)

    p = sub.add_parser("ingest", parents=[extraction, database], help="parse emails and load them into SQLite")
    p.add_argument("--reset", action="store_true", help="delete the database first and rebuild it")
    p.set_defaults(func=cmd_ingest)
    return parser


def _path(value: str) -> Path:
    return Path(value).resolve()


def _display(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT)) if path.is_relative_to(PROJECT_ROOT) else str(path)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
