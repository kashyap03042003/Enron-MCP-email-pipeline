"""Extraction statistics: counts, failure reasons and per-field completeness.

Computes and formats only; reports.py writes the files."""

from collections import Counter

from .extract import ExtractionResult
from .models import MANDATORY_FIELDS, OPTIONAL_FIELDS
from .utils import utc_now


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
        "generated_at": utc_now().isoformat(timespec="seconds"),
        "mailboxes": mailboxes,
        "total_files": result.total_files,
        "parsed": parsed,
        "failed": len(result.failures),
        "failure_reasons": dict(Counter(_reason_category(r) for _, r in result.failures).most_common()),
        "field_completeness": completeness,
    }


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
