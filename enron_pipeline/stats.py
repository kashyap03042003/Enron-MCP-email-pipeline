"""Extraction statistics and the parse-failure log."""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .extract import ExtractionResult
from .models import MANDATORY_FIELDS, OPTIONAL_FIELDS


def build_stats(result: ExtractionResult, mailboxes: list[str]) -> dict:
    parsed = len(result.emails)
    completeness = {}
    for name in MANDATORY_FIELDS + OPTIONAL_FIELDS:
        # "Present" means a non-empty value: an empty subject or an empty
        # CC list counts as absent, as does has_attachment=False.
        present = sum(1 for email in result.emails if getattr(email, name))
        completeness[name] = {
            "present": present,
            "rate": round(present / parsed * 100, 2) if parsed else 0.0,
        }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mailboxes": mailboxes,
        "total_files": result.total_files,
        "parsed": parsed,
        "failed": len(result.failures),
        "failure_reasons": dict(Counter(_reason_category(r) for _, r in result.failures).most_common()),
        "field_completeness": completeness,
    }


def write_stats(stats: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stats, indent=2) + "\n")


def write_error_log(failures: list[tuple[str, str]], path: Path) -> None:
    """One line per failed file: <source_file><TAB><reason>."""
    with path.open("w", encoding="utf-8") as fh:
        fh.write(f"# Parse failures: {len(failures)}\n# source_file\treason\n")
        for source_file, reason in failures:
            fh.write(f"{source_file}\t{reason}\n")


def format_stats(stats: dict) -> str:
    lines = [
        f"Mailboxes:   {', '.join(stats['mailboxes'])}",
        f"Files found: {stats['total_files']:,}",
        f"Parsed:      {stats['parsed']:,}",
        f"Failed:      {stats['failed']:,}",
    ]
    if stats["failure_reasons"]:
        lines.append("\nFailure reasons:")
        lines += [f"  {count:>7,}  {reason}" for reason, count in stats["failure_reasons"].items()]
    lines.append("\nField completeness (share of parsed emails with a non-empty value):")
    for name, entry in stats["field_completeness"].items():
        marker = "*" if name in MANDATORY_FIELDS else " "
        lines.append(f"  {marker} {name:<18} {entry['rate']:>6.2f}%  ({entry['present']:,})")
    lines.append("  (* = mandatory)")
    return "\n".join(lines)


def _reason_category(reason: str) -> str:
    # "unparseable Date header: 'xyz'" -> "unparseable Date header"
    return reason.split(":", 1)[0]
