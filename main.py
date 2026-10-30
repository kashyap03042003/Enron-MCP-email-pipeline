"""Enron email pipeline — command-line entry point.

    python main.py                              # full pipeline: ingest -> dedupe -> notify (dry run)
    python main.py --send-live --limit 5        # same, and send up to 5 notices via MCP

Each stage can also run on its own: `ingest`, `dedupe`, `notify`.
Run `python main.py --help` for all options.
"""

import argparse
import sys
from pathlib import Path

from enron_pipeline import db, dedupe, notify, reports
from enron_pipeline.config import (
    DB_PATH, DEDUPE_STATS, DEFAULT_MAILBOXES, DUPLICATES_REPORT, ERROR_LOG, EXTRACTION_STATS,
    MAILDIR, MCP_CONFIG, PROJECT_ROOT, REPLIES_DIR, SCHEMA_PATH, SEND_LOG, SIMILARITY_THRESHOLD,
)
from enron_pipeline.discovery import discover_emails
from enron_pipeline.extract import extract
from enron_pipeline.mcp_client import McpEmailSender, load_server_config
from enron_pipeline.stats import build_stats, format_stats
from enron_pipeline.utils import utc_now


def ingest(args: argparse.Namespace) -> None:
    """Stage 1+2: parse the mailboxes, log failures and stats, load SQLite."""
    result = extract(discover_emails(MAILDIR, args.mailboxes), PROJECT_ROOT)
    stats = build_stats(result, args.mailboxes)
    reports.write_json(stats, EXTRACTION_STATS)
    reports.write_error_log(result.failures, ERROR_LOG)
    print(format_stats(stats))

    if args.reset and args.db.exists():
        args.db.unlink()
    conn = db.connect(args.db)
    try:
        db.init_schema(conn, SCHEMA_PATH)
        loaded = db.load_emails(conn, result.emails)
    finally:
        conn.close()
    print(f"\nLoaded into {args.db.name}: {loaded.inserted:,} inserted, "
          f"{loaded.skipped:,} skipped (already in database)")


def find_duplicates(args: argparse.Namespace) -> None:
    """Stage 3: flag near-duplicates, write the report and stats."""
    conn = db.connect(args.db)
    try:
        db.init_schema(conn, SCHEMA_PATH)
        candidates = db.load_candidates(conn)
        groups = dedupe.find_duplicate_groups(candidates, args.threshold)
        db.save_duplicate_flags(conn, groups)
    finally:
        conn.close()

    reports.write_duplicates_report(groups, DUPLICATES_REPORT)
    stats = dedupe.summarize(groups, len(candidates), args.threshold)
    reports.write_json(stats, DEDUPE_STATS)
    print(f"Emails scanned:      {stats['emails_scanned']:,}")
    print(f"Duplicate groups:    {stats['groups']:,}")
    print(f"Emails flagged:      {stats['emails_flagged']:,}")
    print(f"Average group size:  {stats['average_group_size']}")
    print(f"Report: {DUPLICATES_REPORT.name}")


def send_notices(args: argparse.Namespace) -> None:
    """Stage 4: write one .eml draft per duplicate group; with --send-live, send via MCP."""
    conn = db.connect(args.db)
    try:
        db.init_schema(conn, SCHEMA_PATH)
        # Dry run: a draft for every flagged email. Live: one notice per group,
        # to the sender of its latest duplicate (assignment sections 7 and 4.2).
        drafts = notify.load_notifications(conn, all_flagged=True)
        now = utc_now()
        reports.write_drafts([notify.to_eml(n, now) for n in drafts], REPLIES_DIR)
        print(f"Drafts written: {len(drafts):,} -> output/replies/")
        notifications = notify.load_notifications(conn)
        if not args.send_live:
            print("Dry run: nothing was sent. Add --send-live to send through the MCP server.")
            return

        if not args.mcp_config.exists():
            sys.exit(f"error: {args.mcp_config.name} not found. Copy mcp_config.json.example "
                     "to mcp_config.json and fill in your Gmail credentials (see README).")
        server, server_env = load_server_config(args.mcp_config)
        # Notices go to a test inbox, never to the real (Enron) senders.
        recipient = args.test_recipient or server_env.get("GMAIL_ADDRESS")
        if not recipient:
            sys.exit("error: no test inbox. Pass --test-recipient or set GMAIL_ADDRESS in the MCP config.")

        print(f"Live mode: sending up to {args.limit} notice(s) to {recipient} via MCP send_email")
        with McpEmailSender(server) as sender:
            summary = notify.send_live(conn, notifications, sender, recipient, SEND_LOG, args.limit)
        print(f"Sent: {summary.sent}   Failed: {summary.failed}   "
              f"Skipped: {summary.skipped} (notified in an earlier run)   Log: output/send_log.csv")
    finally:
        conn.close()


def run_all(args: argparse.Namespace) -> None:
    for title, stage in (("Extract + load", ingest), ("Duplicate detection", find_duplicates),
                         ("Notifications", send_notices)):
        print(f"\n=== {title} ===")
        stage(args)


STAGES = {"run": run_all, "ingest": ingest, "dedupe": find_duplicates, "notify": send_notices}


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enron email extraction pipeline")
    parser.add_argument("stage", nargs="?", default="run", choices=STAGES,
                        help="run = all stages in order (default)")
    parser.add_argument("--mailboxes", nargs="+", default=list(DEFAULT_MAILBOXES))
    parser.add_argument("--db", type=Path, default=DB_PATH, help="SQLite database file")
    parser.add_argument("--reset", action="store_true", help="delete the database first and rebuild it")
    parser.add_argument("--threshold", type=float, default=SIMILARITY_THRESHOLD,
                        help="minimum content similarity, 0-100 (default: %(default)s)")
    parser.add_argument("--send-live", action="store_true",
                        help="actually send notices through the MCP server (default: dry run)")
    parser.add_argument("--limit", type=int, default=5,
                        help="maximum notices to send in this run (default: %(default)s)")
    parser.add_argument("--test-recipient",
                        help="inbox for live notices (default: GMAIL_ADDRESS from the MCP config)")
    parser.add_argument("--mcp-config", type=Path, default=MCP_CONFIG,
                        help="mcpServers config file (default: mcp_config.json)")
    args = parser.parse_args(argv)
    if not 0 <= args.threshold <= 100:
        parser.error("--threshold must be between 0 and 100")
    if args.limit < 1:
        parser.error("--limit must be at least 1")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    STAGES[args.stage](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
