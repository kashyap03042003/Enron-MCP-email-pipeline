import csv
from datetime import datetime, timezone
from email import message_from_bytes
from email.policy import default

import pytest

from enron_pipeline import db, dedupe, notify, reports
from enron_pipeline.config import SCHEMA_PATH
from enron_pipeline.models import ParsedEmail

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
BODY = "Please review the attached budget numbers before Friday's meeting with the board."


def parsed(n: int, day: int, sender: str = "a@enron.com", subject: str = "Budget", body: str = BODY):
    return ParsedEmail(
        message_id=f"<{n}@x>", date=datetime(2001, 5, day, tzinfo=timezone.utc),
        from_address=sender, to_addresses=["z@enron.com"], subject=subject, body=body,
        source_file=f"maildir/a/inbox/{n}.",
    )


@pytest.fixture
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn, SCHEMA_PATH)
    db.load_emails(conn, [
        # group 1: original <1>, duplicates <2>, <3> (latest)
        parsed(1, 1, subject="Budget"), parsed(2, 2, subject="RE: Budget"), parsed(3, 3, subject="Budget"),
        # group 2: original <4>, duplicate <5>
        parsed(4, 4, sender="b@enron.com", subject="Lunch", body="Lunch at noon on Friday at the usual place?"),
        parsed(5, 5, sender="b@enron.com", subject="Lunch", body="Lunch at noon on Friday at the usual place?"),
        parsed(6, 6, sender="c@enron.com", subject="Solo"),
    ])
    db.save_duplicate_flags(conn, dedupe.find_duplicate_groups(db.load_candidates(conn), 90.0))
    yield conn
    conn.close()


class FakeSender:
    def __init__(self, fail_on: set[str] = frozenset()):
        self.sent: list[dict] = []
        self.fail_on = fail_on

    def send(self, *, to, subject, body, headers):
        if headers["References"] in self.fail_on:
            raise ConnectionError("SMTP unavailable")
        self.sent.append({"to": to, "subject": subject, "body": body, "headers": headers})


def test_one_notice_per_group_for_the_latest_duplicate(conn):
    notices = notify.load_notifications(conn)
    assert [(n.to, n.references) for n in notices] == [("a@enron.com", "<3@x>"), ("b@enron.com", "<5@x>")]


def test_notice_follows_the_template(conn):
    notice = notify.load_notifications(conn)[0]
    assert notice.subject == "[Duplicate Notice] Re: Budget"  # the original's subject
    assert "Message-ID:  <3@x>\n    Date Sent:   2001-05-03 00:00:00 UTC\n    Subject:     Budget" in notice.body
    assert "Original Email on Record:\n    Message-ID:  <1@x>\n    Date Sent:   2001-05-01 00:00:00 UTC" in notice.body
    assert "Similarity Score: 100.00%" in notice.body
    assert "please reply with CONFIRM to restore it to active status." in notice.body


def test_eml_headers(conn):
    msg = notify.to_eml(notify.load_notifications(conn)[0], NOW)
    assert msg["To"] == "a@enron.com"
    assert msg["Subject"] == "[Duplicate Notice] Re: Budget"
    assert msg["Date"] == "Thu, 29 Oct 2026 12:00:00 +0000"
    assert msg["References"] == "<3@x>"


def test_write_drafts_replaces_previous_drafts(conn, tmp_path):
    out = tmp_path / "replies"
    out.mkdir()
    (out / "stale.eml").write_text("old")
    assert reports.write_drafts([notify.to_eml(n, NOW) for n in notify.load_notifications(conn)], out) == 2
    files = sorted(p.name for p in out.glob("*.eml"))
    assert files == ["3@x.eml", "5@x.eml"]
    parsed_back = message_from_bytes((out / "3@x.eml").read_bytes(), policy=default)
    assert parsed_back["References"] == "<3@x>"


def test_live_send_redirects_to_test_inbox_and_marks_db(conn, tmp_path):
    sender, log = FakeSender(), tmp_path / "send_log.csv"
    summary = notify.send_live(conn, notify.load_notifications(conn), sender, "me@gmail.com", log, limit=None)

    assert (summary.sent, summary.failed, summary.skipped) == (2, 0, 0)
    first = sender.sent[0]
    assert first["to"] == "me@gmail.com"
    assert first["headers"] == {"References": "<3@x>", "X-Original-To": "a@enron.com"}
    assert first["body"] == notify.load_notifications(conn)[0].body  # exactly the template

    row = conn.execute("SELECT notification_sent, notification_date FROM emails WHERE message_id = '<3@x>'").fetchone()
    assert row["notification_sent"] == 1 and row["notification_date"] is not None
    with log.open() as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0]) == list(reports.SEND_LOG_COLUMNS)
    assert [(r["recipient"], r["status"], r["intended_recipient"]) for r in rows] == [
        ("me@gmail.com", "sent", "a@enron.com"), ("me@gmail.com", "sent", "b@enron.com"),
    ]


def test_limit_caps_sends_and_next_run_resumes(conn, tmp_path):
    log = tmp_path / "send_log.csv"
    first = notify.send_live(conn, notify.load_notifications(conn), FakeSender(), "me@gmail.com", log, limit=1)
    second_sender = FakeSender()
    second = notify.send_live(conn, notify.load_notifications(conn), second_sender, "me@gmail.com", log, limit=1)

    assert (first.sent, second.sent, second.skipped) == (1, 1, 1)
    assert second_sender.sent[0]["headers"]["References"] == "<5@x>"
    with log.open() as fh:
        assert len(list(csv.DictReader(fh))) == 2  # header written once, log appended


def test_failed_send_is_logged_and_does_not_stop_the_run(conn, tmp_path):
    log = tmp_path / "send_log.csv"
    summary = notify.send_live(conn, notify.load_notifications(conn), FakeSender(fail_on={"<3@x>"}),
                               "me@gmail.com", log, limit=None)
    assert (summary.sent, summary.failed) == (1, 1)
    assert conn.execute("SELECT notification_sent FROM emails WHERE message_id = '<3@x>'").fetchone()[0] == 0
    with log.open() as fh:
        failed = next(r for r in csv.DictReader(fh) if r["status"] == "failed")
    assert failed["error"] == "ConnectionError: SMTP unavailable"


# --- regressions from code review --------------------------------------------

def test_drafts_cover_every_flagged_email(conn):
    drafts = notify.load_notifications(conn, all_flagged=True)
    assert [n.references for n in drafts] == ["<2@x>", "<3@x>", "<5@x>"]
    assert [n.to for n in drafts] == ["a@enron.com", "a@enron.com", "b@enron.com"]


def test_delivered_notice_is_recorded_even_if_logging_fails(conn, tmp_path):
    unwritable_log = tmp_path / "is_a_directory"
    unwritable_log.mkdir()
    with pytest.raises(OSError):
        notify.send_live(conn, notify.load_notifications(conn), FakeSender(), "me@gmail.com",
                         unwritable_log, limit=1)
    # The send succeeded, so it must not be retried on the next run.
    assert conn.execute("SELECT notification_sent FROM emails WHERE message_id = '<3@x>'").fetchone()[0] == 1
