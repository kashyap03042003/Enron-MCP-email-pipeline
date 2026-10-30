"""Task 4: duplicate notices — dry-run .eml drafts and live sending.

One notice per duplicate group, addressed to the sender of the group's
latest duplicate. Targets are read from the database, so this stage only
depends on the flags written by `dedupe`.

Live sends are always redirected to a test inbox: the real recipients are
(often still live) Enron addresses. The intended recipient is kept in an
X-Original-To header and a note at the top of the body.
"""

import csv
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from typing import Protocol

from .db import to_db_datetime

SEND_LOG_COLUMNS = (
    "timestamp", "recipient", "subject", "status", "error",
    "intended_recipient", "duplicate_message_id",
)

_BODY_TEMPLATE = """\
This is an automated notification from the Email Deduplication System.

Your email has been identified as a potential duplicate:

  Your Email (Flagged):
    Message-ID:  {duplicate_message_id}
    Date Sent:   {duplicate_date} UTC
    Subject:     {duplicate_subject}

  Original Email on Record:
    Message-ID:  {original_message_id}
    Date Sent:   {original_date} UTC

  Similarity Score: {similarity:.2f}%

If this was NOT a duplicate and you intended to send this email,
please reply with CONFIRM to restore it to active status.

No action is required if this is indeed a duplicate.
"""

# The latest flagged email of each group (ties broken by source_file, as in
# dedupe), together with that group's original.
_TARGETS_SQL = """
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


@dataclass(frozen=True)
class Notification:
    to: str
    subject: str
    references: str  # message_id of the latest duplicate
    body: str
    already_sent: bool = False


class Sender(Protocol):
    def send(self, *, to: str, subject: str, body: str, headers: dict[str, str]) -> None:
        """Deliver one email; raise on failure."""


@dataclass
class SendSummary:
    sent: int = 0
    failed: int = 0
    skipped: int = 0  # already notified in an earlier run


def load_notifications(conn: sqlite3.Connection) -> list[Notification]:
    return [
        Notification(
            to=row["from_address"],
            subject=f"[Duplicate Notice] Re: {row['original_subject']}",
            references=row["message_id"],
            body=_BODY_TEMPLATE.format(
                duplicate_message_id=row["message_id"],
                duplicate_date=row["date"],
                duplicate_subject=row["subject"],
                original_message_id=row["original_message_id"],
                original_date=row["original_date"],
                similarity=row["similarity_score"],
            ),
            already_sent=bool(row["notification_sent"]),
        )
        for row in conn.execute(_TARGETS_SQL)
    ]


def to_eml(notification: Notification, now: datetime) -> EmailMessage:
    msg = EmailMessage()
    msg["To"] = notification.to
    msg["Subject"] = notification.subject
    msg["Date"] = format_datetime(now)
    msg["References"] = notification.references
    msg.set_content(notification.body)
    return msg


def write_drafts(notifications: list[Notification], out_dir: Path, now: datetime) -> int:
    """Write one .eml per notification, replacing drafts from earlier runs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.eml"):
        old.unlink()
    for notification in notifications:
        path = out_dir / f"{_safe_filename(notification.references)}.eml"
        path.write_bytes(to_eml(notification, now).as_bytes())
    return len(notifications)


def send_live(
    conn: sqlite3.Connection,
    notifications: list[Notification],
    sender: Sender,
    test_recipient: str,
    log_path: Path,
    limit: int | None,
) -> SendSummary:
    """Send pending notices to the test inbox, logging each attempt.

    A notice whose duplicate is already marked notification_sent is skipped,
    so repeated runs carry on where the last one stopped. `limit` caps the
    number of send attempts per run.
    """
    summary = SendSummary()
    with SendLog(log_path) as log:
        for notification in notifications:
            if notification.already_sent:
                summary.skipped += 1
                continue
            if limit is not None and summary.sent + summary.failed >= limit:
                break
            now = datetime.now(timezone.utc)
            try:
                sender.send(
                    to=test_recipient,
                    subject=notification.subject,
                    body=f"[Test delivery: intended for {notification.to}]\n\n{notification.body}",
                    headers={
                        "References": notification.references,
                        "X-Original-To": notification.to,
                    },
                )
            except Exception as exc:  # one failed send must not stop the rest
                summary.failed += 1
                log.write(now, test_recipient, notification, "failed", f"{type(exc).__name__}: {exc}")
                continue
            summary.sent += 1
            log.write(now, test_recipient, notification, "sent", "")
            with conn:
                conn.execute(
                    "UPDATE emails SET notification_sent = 1, notification_date = ? WHERE message_id = ?",
                    (to_db_datetime(now), notification.references),
                )
    return summary


class SendLog:
    """Append-only CSV of live send attempts."""

    def __init__(self, path: Path):
        self._path = path

    def __enter__(self) -> "SendLog":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not self._path.exists() or self._path.stat().st_size == 0
        self._fh = self._path.open("a", newline="", encoding="utf-8")
        self._writer = csv.writer(self._fh)
        if is_new:
            self._writer.writerow(SEND_LOG_COLUMNS)
        return self

    def __exit__(self, *exc_info) -> None:
        self._fh.close()

    def write(self, when: datetime, recipient: str, notification: Notification, status: str, error: str) -> None:
        self._writer.writerow([
            when.isoformat(timespec="seconds"), recipient, notification.subject, status, error,
            notification.to, notification.references,
        ])
        self._fh.flush()  # keep the log accurate even if the run is interrupted


def _safe_filename(message_id: str) -> str:
    return re.sub(r"[^\w.@-]", "_", message_id.strip("<>"))
