"""Enron email pipeline — command-line entry point.

    python main.py run     [--reset] [--send-live [--limit N]]  # full pipeline: ingest -> dedupe -> notify

    python main.py parse   [--mailboxes ...] [--max-files N]  # extraction stats only, no DB
    python main.py ingest  [--mailboxes ...] [--reset]        # parse + load into SQLite
    python main.py dedupe  [--threshold 90]                   # flag near-duplicates
    python main.py notify  [--send-live [--limit N]]          # .eml drafts; optionally send via MCP
"""

import argparse
import os
import sys
import time
from itertools import islice
from pathlib import Path

from datetime import datetime, timezone

from enron_pipeline import db, dedupe, notify
from enron_pipeline.config import (
    DB_PATH, DEDUPE_STATS, DEFAULT_MAILBOXES, DUPLICATES_REPORT, ERROR_LOG, EXTRACTION_STATS,
    MAILDIR, MCP_CONFIG, PROJECT_ROOT, REPLIES_DIR, SCHEMA_PATH, SEND_LOG, SIMILARITY_THRESHOLD,
)
from enron_pipeline.discovery import discover_emails
from enron_pipeline.extract import ExtractionResult, extract
from enron_pipeline.mcp_client import McpEmailSender, load_server_config
from enron_pipeline.stats import build_stats, format_stats, write_error_log, write_stats


def run_extraction(args: argparse.Namespace) -> ExtractionResult:
    paths = discover_emails(MAILDIR, args.mailboxes)
    if args.max_files:
        paths = islice(paths, args.max_files)

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


def cmd_dedupe(args: argparse.Namespace) -> None:
    conn = db.connect(args.db)
    try:
        db.init_schema(conn, SCHEMA_PATH)
        started = time.perf_counter()
        candidates = dedupe.load_candidates(conn)
        groups = dedupe.find_duplicate_groups(candidates, args.threshold)
        dedupe.apply_flags(conn, groups)
        elapsed = time.perf_counter() - started
    finally:
        conn.close()

    dedupe.write_report(groups, DUPLICATES_REPORT)
    stats = dedupe.summarize(groups, len(candidates), args.threshold)
    write_stats(stats, DEDUPE_STATS)

    print(f"Emails scanned:      {stats['emails_scanned']:,}")
    print(f"Similarity cutoff:   {stats['threshold']}%")
    print(f"Duplicate groups:    {stats['groups']:,}")
    print(f"Emails flagged:      {stats['emails_flagged']:,}")
    print(f"Average group size:  {stats['average_group_size']}")
    print(f"Largest group:       {stats['largest_group']}")
    print(f"\nScanned in {elapsed:.1f}s")
    print(f"Report:     {_display(DUPLICATES_REPORT)}")
    print(f"Stats JSON: {_display(DEDUPE_STATS)}")


def cmd_notify(args: argparse.Namespace) -> None:
    conn = db.connect(args.db)
    try:
        db.init_schema(conn, SCHEMA_PATH)
        notifications = notify.load_notifications(conn)
        written = notify.write_drafts(notifications, REPLIES_DIR, datetime.now(timezone.utc))
        print(f"Notices (one per duplicate group): {len(notifications):,}")
        print(f"Drafts written:                    {written:,}  -> {_display(REPLIES_DIR)}/")
        if not args.send_live:
            print("Dry run: nothing was sent. Add --send-live to send through the MCP server.")
            return
        _send_live(args, conn, notifications)
    finally:
        conn.close()


def _send_live(args, conn, notifications) -> None:
    if not args.mcp_config.exists():
        sys.exit(f"error: {_display(args.mcp_config)} not found. Copy mcp_config.json.example "
                 "to mcp_config.json and fill in your Gmail credentials (see README).")
    server, server_env = load_server_config(args.mcp_config)
    # Notices go to a test inbox, never to the real (Enron) senders. Default:
    # the Gmail account the server sends from.
    recipient = args.test_recipient or os.environ.get("NOTIFY_TEST_RECIPIENT") or server_env.get("GMAIL_ADDRESS")
    if not recipient:
        sys.exit("error: no test inbox. Pass --test-recipient or set GMAIL_ADDRESS in the MCP config.")

    print(f"\nLive mode: sending up to {args.limit} notice(s) to {recipient} via MCP send_email")
    with McpEmailSender(server) as sender:
        summary = notify.send_live(conn, notifications, sender, recipient, SEND_LOG, args.limit)
    print(f"Sent:    {summary.sent}")
    print(f"Failed:  {summary.failed}")
    print(f"Skipped: {summary.skipped}  (notified in an earlier run)")
    print(f"Log:     {_display(SEND_LOG)}")


def cmd_run(args: argparse.Namespace) -> None:
    for title, step in (("Extract + load", cmd_ingest), ("Duplicate detection", cmd_dedupe),
                        ("Notifications", cmd_notify)):
        print(f"\n=== {title} " + "=" * (60 - len(title)))
        step(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enron email extraction pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    extraction = argparse.ArgumentParser(add_help=False)
    extraction.add_argument("--mailboxes", nargs="+", default=list(DEFAULT_MAILBOXES))
    extraction.add_argument("--max-files", type=int, help="only parse the first N files (for quick runs)")

    database = argparse.ArgumentParser(add_help=False)
    database.add_argument("--db", type=_path, default=DB_PATH, help="SQLite database file")

    reset =argparse.ArgumentParser(add_help=False)
    reset.add_argument("--reset", action="store_true", help="delete the database first and rebuild it")

    threshold = argparse.ArgumentParser(add_help=False)
    threshold.add_argument("--threshold", type=float, default=SIMILARITY_THRESHOLD,
                           help="minimum content similarity, 0-100 (default: %(default)s)")

    live = argparse.ArgumentParser(add_help=False)
    live.add_argument("--send-live", action="store_true",
                      help="actually send notices through the MCP server (default: dry run)")
    live.add_argument("--limit", type=_positive_int, default=5,
                      help="maximum notices to send in this run (default: %(default)s)")
    live.add_argument("--test-recipient",
                      help="inbox that receives live notices (default: GMAIL_ADDRESS from the MCP config)")
    live.add_argument("--mcp-config", type=_path, default=MCP_CONFIG,
                      help="mcpServers config file (default: mcp_config.json)")

    p = sub.add_parser("run", parents=[extraction, database, reset, threshold, live],
                       help="run the full pipeline end to end")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("parse", parents=[extraction], help="parse emails and print extraction statistics")
    p.set_defaults(func=cmd_parse)

    p = sub.add_parser("ingest", parents=[extraction, database, reset], help="parse emails and load them into SQLite")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("dedupe", parents=[database, threshold], help="flag near-duplicate emails in the database")
    p.set_defaults(func=cmd_dedupe)

    p = sub.add_parser("notify", parents=[database, live], help="write duplicate notices as .eml drafts")
    p.set_defaults(func=cmd_notify)
    return parser


def _path(value: str) -> Path:
    return Path(value).resolve()


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def _display(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT)) if path.is_relative_to(PROJECT_ROOT) else str(path)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
