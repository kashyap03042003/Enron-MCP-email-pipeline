import sqlite3
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from enron_pipeline import db
from enron_pipeline.config import PROJECT_ROOT, SCHEMA_PATH
from enron_pipeline.models import ParsedEmail


def make_email(n: int = 1, **overrides) -> ParsedEmail:
    email = ParsedEmail(
        message_id=f"<{n}@thyme>",
        date=datetime(2001, 5, 14, 23, 39, tzinfo=timezone.utc),
        from_address="jeff.skilling@enron.com",
        to_addresses=["kenneth.lay@enron.com", "a@enron.com"],
        subject="Budget",
        body="Numbers attached.",
        source_file=f"maildir/skilling-j/sent/{n}.",
        cc_addresses=["b@enron.com"],
        headings=["SUMMARY", "NEXT STEPS"],
        has_attachment=True,
    )
    return replace(email, **overrides)


@pytest.fixture
def conn(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    db.init_schema(conn, SCHEMA_PATH)
    yield conn
    conn.close()


def test_schema_can_be_applied_twice(conn):
    db.init_schema(conn, SCHEMA_PATH)


def test_email_round_trip(conn):
    db.load_emails(conn, [make_email()])
    row = conn.execute("SELECT * FROM emails").fetchone()
    assert row["message_id"] == "<1@thyme>"
    assert row["date"] == "2001-05-14 23:39:00"
    assert row["from_address"] == "jeff.skilling@enron.com"
    assert row["headings"] == "SUMMARY\nNEXT STEPS"
    assert row["has_attachment"] == 1
    assert row["is_duplicate"] == 0 and row["duplicate_of"] is None
    assert row["notification_sent"] == 0 and row["notification_date"] is None


def test_recipients_are_normalized_with_order(conn):
    db.load_emails(conn, [make_email()])
    rows = conn.execute(
        "SELECT r.kind, r.position, a.email FROM email_recipients r "
        "JOIN addresses a ON a.id = r.address_id ORDER BY r.kind DESC, r.position"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("to", 0, "kenneth.lay@enron.com"), ("to", 1, "a@enron.com"), ("cc", 0, "b@enron.com"),
    ]


def test_recipient_addresses_are_stored_once(conn):
    second = make_email(2, to_addresses=["kenneth.lay@enron.com"], cc_addresses=["b@enron.com"])
    db.load_emails(conn, [make_email(), second])
    # ken, a, b: each stored once although ken and b are recipients of both emails
    assert conn.execute("SELECT COUNT(*) FROM addresses").fetchone()[0] == 3


def test_reloading_skips_existing_emails(conn):
    first = db.load_emails(conn, [make_email(1), make_email(2)])
    second = db.load_emails(conn, [make_email(1), make_email(2), make_email(3)])
    assert (first.inserted, first.skipped) == (2, 0)
    assert (second.inserted, second.skipped) == (1, 2)
    assert conn.execute("SELECT COUNT(*) FROM email_recipients").fetchone()[0] == 9


def test_message_id_is_unique(conn):
    db.load_emails(conn, [make_email(1)])
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO emails (message_id, date, from_address, subject, body, source_file) "
            "VALUES ('<1@thyme>', '2001-01-01 00:00:00', 'x@y.com', '', '', 'other')"
        )


def test_duplicate_flag_requires_duplicate_of(conn):
    db.load_emails(conn, [make_email()])
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE emails SET is_duplicate = 1")


def test_duplicate_of_must_reference_an_existing_email(conn):
    db.load_emails(conn, [make_email()])
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE emails SET is_duplicate = 1, duplicate_of = '<missing@x>'")


def test_required_indexes_exist(conn):
    indexed = {
        (row["tbl_name"], col["name"])
        for row in conn.execute("SELECT name, tbl_name FROM sqlite_master WHERE type = 'index'")
        for col in conn.execute(f"PRAGMA index_info('{row['name']}')")
    }
    assert {("emails", "date"), ("emails", "from_address"), ("emails", "subject")} <= indexed


def test_sample_queries_run(conn):
    db.load_emails(conn, [make_email(1), make_email(2)])
    script = (PROJECT_ROOT / "sample_queries.sql").read_text()
    statements = [s for s in sqlite3_split(script) if s.strip()]
    assert len(statements) >= 3
    for statement in statements:
        conn.execute(statement).fetchall()


def sqlite3_split(script: str) -> list[str]:
    """Split a SQL script into complete statements (comments stripped)."""
    statements, buffer = [], ""
    for line in script.splitlines():
        if line.lstrip().startswith("--"):
            continue
        buffer += line + "\n"
        if sqlite3.complete_statement(buffer):
            statements.append(buffer)
            buffer = ""
    return statements
