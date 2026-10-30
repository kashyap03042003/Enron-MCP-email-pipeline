"""Output files. Every file the pipeline writes is produced here.

    output/extraction_stats.json, output/dedupe_stats.json   write_json
    error_log.txt                                            write_error_log
    duplicates_report.csv                                    write_duplicates_report
    output/replies/*.eml                                     write_drafts
    output/send_log.csv                                      append_send_log
"""

import csv
import json
from email.message import EmailMessage
from pathlib import Path

from .dedupe import DuplicateGroup
from .utils import safe_filename

DUPLICATES_REPORT_COLUMNS = (
    "duplicate_message_id", "original_message_id", "subject", "from_address",
    "duplicate_date", "original_date", "similarity_score",
)
SEND_LOG_COLUMNS = (
    "timestamp", "recipient", "subject", "status", "error",
    "intended_recipient", "duplicate_message_id",
)


def write_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def write_error_log(failures: list[tuple[str, str]], path: Path) -> None:
    """One line per failed file: <source_file><TAB><reason>."""
    with path.open("w", encoding="utf-8") as fh:
        fh.write(f"# Parse failures: {len(failures)}\n# source_file\treason\n")
        for source_file, reason in failures:
            fh.write(f"{source_file}\t{reason}\n")


def write_duplicates_report(groups: list[DuplicateGroup], path: Path) -> None:
    """One row per flagged email, next to the original it duplicates."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(DUPLICATES_REPORT_COLUMNS)
        for group in groups:
            for duplicate, score in group.duplicates:
                writer.writerow([
                    duplicate.message_id, group.original.message_id, duplicate.subject,
                    duplicate.from_address, duplicate.date, group.original.date, f"{score:.2f}",
                ])


def write_drafts(messages: list[EmailMessage], out_dir: Path) -> int:
    """Write one .eml per message, named after its References header, replacing
    drafts from earlier runs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.eml"):
        old.unlink()
    for msg in messages:
        (out_dir / f"{safe_filename(msg['References'])}.eml").write_bytes(msg.as_bytes())
    return len(messages)


def append_send_log(path: Path, row: list[str]) -> None:
    """Append one send attempt (columns: SEND_LOG_COLUMNS), adding the header
    to a new file. The file is opened per row so the log stays complete even
    if a run is interrupted."""
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if is_new:
            writer.writerow(SEND_LOG_COLUMNS)
        writer.writerow(row)
