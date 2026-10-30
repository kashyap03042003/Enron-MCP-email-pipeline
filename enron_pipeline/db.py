"""SQLite storage: apply the schema and load parsed emails."""

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .models import ParsedEmail

_EMAIL_COLUMNS = (
    "message_id", "date", "sender_id", "subject", "body", "source_file",
    "x_from", "x_to", "x_cc", "x_bcc", "x_folder", "x_origin", "content_type",
    "has_attachment", "forwarded_content", "quoted_content", "headings",
)
_INSERT_EMAIL = (
    f"INSERT INTO emails ({', '.join(_EMAIL_COLUMNS)}) "
    f"VALUES ({', '.join('?' * len(_EMAIL_COLUMNS))}) "
    "ON CONFLICT DO NOTHING"  # message_id or source_file already loaded
)


@dataclass
class LoadResult:
    inserted: int = 0
    skipped: int = 0  # already in the database (same message_id or source_file)


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection, schema_path: Path) -> None:
    conn.executescript(schema_path.read_text())


def to_db_datetime(dt: datetime) -> str:
    """Aware datetime -> 'YYYY-MM-DD HH:MM:SS' in UTC."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def load_emails(conn: sqlite3.Connection, emails: Iterable[ParsedEmail]) -> LoadResult:
    """Insert emails and their recipients in a single transaction."""
    result = LoadResult()
    address_ids = _AddressCache(conn)
    with conn:
        for email in emails:
            cursor = conn.execute(_INSERT_EMAIL, _email_row(email, address_ids(email.from_address)))
            if cursor.rowcount == 0:
                result.skipped += 1
                continue
            result.inserted += 1
            email_id = cursor.lastrowid
            conn.executemany(
                "INSERT INTO email_recipients (email_id, address_id, kind, position) VALUES (?, ?, ?, ?)",
                [
                    (email_id, address_ids(address), kind, position)
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


class _AddressCache:
    """Maps an address to its addresses.id, inserting it on first sight."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._ids = {row["email"]: row["id"] for row in conn.execute("SELECT id, email FROM addresses")}

    def __call__(self, email: str) -> int:
        if email not in self._ids:
            cursor = self._conn.execute("INSERT INTO addresses (email) VALUES (?)", (email,))
            self._ids[email] = cursor.lastrowid
        return self._ids[email]
