"""SQLite storage. This is the only module that runs SQL.

Every read and write of the database goes through a function here, so a
schema change touches schema.sql and this file only.
"""

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .dedupe import Candidate, DuplicateGroup, normalize_text
from .models import ParsedEmail
from .utils import to_db_datetime

_EMAIL_COLUMNS = (
    "message_id", "date", "sender_id", "subject", "body", "source_file",
    "x_from", "x_to", "x_cc", "x_bcc", "x_folder", "x_origin", "content_type",
    "has_attachment", "forwarded_content", "quoted_content", "headings",
)
_PLACEHOLDERS = ", ".join(["?"] * len(_EMAIL_COLUMNS))
_INSERT_EMAIL = (
    f"INSERT INTO emails ({', '.join(_EMAIL_COLUMNS)}) VALUES ({_PLACEHOLDERS}) "
    "ON CONFLICT DO NOTHING"  # message_id or source_file already loaded
)


@dataclass
class LoadResult:
    inserted: int = 0
    skipped: int = 0  # already in the database (same message_id or source_file)


# --- connection --------------------------------------------------------------

def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection, schema_path: Path) -> None:
    conn.executescript(schema_path.read_text())


# --- Task 2: load parsed emails ---------------------------------------------

def load_emails(conn: sqlite3.Connection, emails: Iterable[ParsedEmail]) -> LoadResult:
    """Insert emails and their recipients in a single transaction."""
    result = LoadResult()
    # address -> addresses.id, so each address is looked up or inserted once
    address_ids = {row["email"]: row["id"] for row in conn.execute("SELECT id, email FROM addresses")}
    with conn:
        for email in emails:
            sender_id = _address_id(conn, address_ids, email.from_address)
            cursor = conn.execute(_INSERT_EMAIL, _email_row(email, sender_id))
            if cursor.rowcount == 0:
                result.skipped += 1
                continue
            result.inserted += 1
            email_id = cursor.lastrowid
            conn.executemany(
                "INSERT INTO email_recipients (email_id, address_id, kind, position) VALUES (?, ?, ?, ?)",
                [
                    (email_id, _address_id(conn, address_ids, address), kind, position)
                    for kind, addresses in (
                        ("to", email.to_addresses),
                        ("cc", email.cc_addresses),
                        ("bcc", email.bcc_addresses),
                    )
                    for position, address in enumerate(addresses)
                ],
            )
    return result


def _email_row(email: ParsedEmail, sender_id: int) -> tuple:
    return (
        email.message_id, to_db_datetime(email.date), sender_id, email.subject,
        email.body, email.source_file, email.x_from, email.x_to, email.x_cc,
        email.x_bcc, email.x_folder, email.x_origin, email.content_type,
        int(email.has_attachment), email.forwarded_content, email.quoted_content,
        "\n".join(email.headings) or None,
    )


def _address_id(conn: sqlite3.Connection, address_ids: dict[str, int], email: str) -> int:
    if email not in address_ids:
        address_ids[email] = conn.execute("INSERT INTO addresses (email) VALUES (?)", (email,)).lastrowid
    return address_ids[email]


# --- Task 3: duplicate flags -------------------------------------------------

def load_candidates(conn: sqlite3.Connection) -> list[Candidate]:
    rows = conn.execute(
        "SELECT message_id, date, from_address, subject, source_file, "
        "body, quoted_content, forwarded_content FROM email_details"
    )
    return [
        Candidate(
            message_id=r["message_id"], date=r["date"], from_address=r["from_address"],
            subject=r["subject"], source_file=r["source_file"],
            body=normalize_text(r["body"]),
            text=normalize_text(r["body"], r["quoted_content"], r["forwarded_content"]),
        )
        for r in rows
    ]


def save_duplicate_flags(conn: sqlite3.Connection, groups: list[DuplicateGroup]) -> None:
    """Replace all duplicate flags with the given groups (a re-scan starts clean).
    Notification columns are left alone so sent notices are never forgotten."""
    with conn:
        conn.execute(
            "UPDATE emails SET is_duplicate = 0, duplicate_of = NULL, similarity_score = NULL "
            "WHERE is_duplicate = 1"
        )
        conn.executemany(
            "UPDATE emails SET is_duplicate = 1, duplicate_of = ?, similarity_score = ? "
            "WHERE message_id = ?",
            [
                (group.original.message_id, score, duplicate.message_id)
                for group in groups
                for duplicate, score in group.duplicates
            ],
        )


# --- Task 4: notification targets and state --------------------------------

# The latest flagged email of each group (ties broken by source_file, as in
# dedupe), together with that group's original.
_NOTIFICATION_TARGETS_SQL = """
SELECT d.message_id, d.date, d.subject, d.from_address, d.similarity_score,
       d.notification_sent,
       o.message_id AS original_message_id, o.date AS original_date,
       o.subject    AS original_subject
FROM email_details d
JOIN emails o ON o.message_id = d.duplicate_of
WHERE d.is_duplicate = 1
  AND NOT EXISTS (
      SELECT 1 FROM emails later
      WHERE later.duplicate_of = d.duplicate_of
        AND (later.date, later.source_file) > (d.date, d.source_file)
  )
ORDER BY o.date, o.source_file
"""


def load_notification_targets(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(_NOTIFICATION_TARGETS_SQL).fetchall()


def mark_notified(conn: sqlite3.Connection, message_id: str, when: datetime) -> None:
    with conn:
        conn.execute(
            "UPDATE emails SET notification_sent = 1, notification_date = ? WHERE message_id = ?",
            (to_db_datetime(when), message_id),
        )
