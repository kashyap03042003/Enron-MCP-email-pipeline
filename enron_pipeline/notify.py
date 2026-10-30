"""Task 4: duplicate notices.

One notice per duplicate group, addressed to the sender of the group's
latest duplicate and following the assignment's template. This module builds
the notices and runs the live send loop; db.py supplies the targets and
records what was sent, and reports.py writes the drafts and the send log.

Live sends are always redirected to a test inbox: the real recipients are
(often still live) Enron addresses. The intended recipient is kept in an
X-Original-To header and a note at the top of the body.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from typing import Protocol

from . import db, reports
from .utils import utc_now

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


def load_notifications(conn: sqlite3.Connection, all_flagged: bool = False) -> list[Notification]:
    """One notice per duplicate group (its latest email), or one per flagged
    email with all_flagged=True."""
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
        for row in db.load_notification_targets(conn, all_flagged)
    ]


def to_eml(notification: Notification, now: datetime) -> EmailMessage:
    msg = EmailMessage()
    msg["To"] = notification.to
    msg["Subject"] = notification.subject
    msg["Date"] = format_datetime(now)
    msg["References"] = notification.references
    msg.set_content(notification.body)
    return msg


def send_live(
    conn: sqlite3.Connection,
    notifications: list[Notification],
    sender: Sender,
    test_recipient: str,
    log_path: Path,
    limit: int | None,
) -> SendSummary:
    """Send pending notices to the test inbox.

    For each notice: send it, record a success in the database, then log the
    attempt. The database is updated before the log so that a logging failure
    can never cause a delivered notice to be sent again. Notices already
    marked as sent are skipped, so repeated runs carry on where the last one
    stopped. `limit` caps send attempts per run.
    """
    summary = SendSummary()
    for notification in notifications:
        if notification.already_sent:
            summary.skipped += 1
            continue
        if limit is not None and summary.sent + summary.failed >= limit:
            break

        now = utc_now()
        try:
            sender.send(
                to=test_recipient,
                subject=notification.subject,
                body=f"[Test delivery: intended for {notification.to}]\n\n{notification.body}",
                headers={"References": notification.references, "X-Original-To": notification.to},
            )
            status, error = "sent", ""
        except Exception as exc:  # one failed send must not stop the rest
            status, error = "failed", f"{type(exc).__name__}: {exc}"

        if status == "sent":
            summary.sent += 1
            db.mark_notified(conn, notification.references, now)
        else:
            summary.failed += 1
        reports.append_send_log(log_path, [
            now.isoformat(timespec="seconds"), test_recipient, notification.subject, status, error,
            notification.to, notification.references,
        ])
    return summary
