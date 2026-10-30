import csv
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from enron_pipeline import db, dedupe
from enron_pipeline.config import SCHEMA_PATH
from enron_pipeline.dedupe import Candidate, find_duplicate_groups, normalize_subject, normalize_text
from enron_pipeline.models import ParsedEmail

BODY = "Please review the attached budget numbers before Friday's meeting with the board."


def cand(n: int, date: str = "2001-05-01 10:00:00", body: str = BODY, *,
         sender="a@enron.com", subject="Budget", quoted="", forwarded="") -> Candidate:
    return Candidate(
        message_id=f"<{n}@x>", date=date, from_address=sender, subject=subject,
        source_file=f"maildir/a/inbox/{n}.", body=normalize_text(body),
        text=normalize_text(body, quoted, forwarded),
    )


def groups_of(*candidates, threshold=90.0):
    return [
        (g.original.message_id, [d.message_id for d, _ in g.duplicates])
        for g in find_duplicate_groups(candidates, threshold)
    ]


@pytest.mark.parametrize("subject, expected", [
    ("Budget", "budget"),
    ("RE: Budget", "budget"),
    ("Re: FW: re:  Budget   Q3", "budget q3"),
    ("Fwd: Budget", "budget"),
    ("RE[2]: Budget", "budget"),
    ("Reply needed", "reply needed"),  # "Re" only stripped as a prefix with a colon
    ("", ""),
])
def test_normalize_subject(subject, expected):
    assert normalize_subject(subject) == expected


def test_normalize_text_collapses_whitespace_and_skips_empty_parts():
    assert normalize_text("a  b\n\nc ", None, "", "d") == "a b c d"


def test_copies_are_grouped_with_earliest_as_original():
    groups = groups_of(cand(2, "2001-05-02 09:00:00"), cand(1, "2001-05-01 09:00:00"))
    assert groups == [("<1@x>", ["<2@x>"])]


def test_group_of_three_flags_all_but_earliest_and_latest_is_last():
    found = find_duplicate_groups(
        [cand(3, "2001-05-03 00:00:00"), cand(1, "2001-05-01 00:00:00"), cand(2, "2001-05-02 00:00:00")],
        90.0,
    )
    assert len(found) == 1
    group = found[0]
    assert group.size == 3
    assert [d.message_id for d, _ in group.duplicates] == ["<2@x>", "<3@x>"]
    assert group.latest[0].message_id == "<3@x>"


def test_subject_prefixes_are_ignored():
    assert groups_of(cand(1, subject="Budget"), cand(2, "2001-05-02 00:00:00", subject="RE: Budget")) == [
        ("<1@x>", ["<2@x>"])
    ]


def test_different_sender_or_subject_is_not_a_duplicate():
    assert groups_of(cand(1), cand(2, sender="b@enron.com")) == []
    assert groups_of(cand(1), cand(2, subject="Other")) == []


def test_small_edits_still_match_but_different_text_does_not():
    edited = BODY.replace("Friday's", "Thursday's")
    assert groups_of(cand(1), cand(2, "2001-05-02 00:00:00", edited)) == [("<1@x>", ["<2@x>"])]
    assert groups_of(cand(1), cand(2, body="Completely unrelated note about lunch plans.")) == []


def test_score_is_reported_and_threshold_respected():
    edited = BODY.replace("Friday's", "Thursday's")
    (group,) = find_duplicate_groups([cand(1), cand(2, "2001-05-02 00:00:00", edited)], 90.0)
    score = group.duplicates[0][1]
    assert 90 <= score < 100
    assert find_duplicate_groups([cand(1), cand(2, "2001-05-02 00:00:00", edited)], score + 1) == []


def test_replies_quoting_the_same_thread_are_not_duplicates():
    thread = "> " + " ".join(["Long original message text that both replies quote."] * 20)
    first = cand(1, body="Sounds good to me.", quoted=thread)
    second = cand(2, "2001-05-02 00:00:00", body="I disagree, let's talk.", quoted=thread)
    assert dedupe.similarity(first, second, 90.0) == 0
    assert groups_of(first, second) == []


def test_pure_forwards_compare_forwarded_content():
    fwd = "-----Original Message----- " + BODY
    other = "-----Original Message----- Quarterly gas storage report attached for review."
    same = groups_of(cand(1, body="", forwarded=fwd), cand(2, "2001-05-02 00:00:00", body="", forwarded=fwd))
    different = groups_of(cand(1, body="", forwarded=fwd), cand(2, "2001-05-02 00:00:00", body="", forwarded=other))
    assert same == [("<1@x>", ["<2@x>"])]
    assert different == []


def test_each_duplicate_is_compared_to_the_original_not_a_chain():
    # B is a light edit of A; C is a light edit of B but too far from A.
    a = "alpha bravo charlie delta echo foxtrot golf hotel india juliet"
    b = "alpha bravo charlie delta echo foxtrot golf hotel india kilo"
    c = "alpha bravo charlie delta echo foxtrot golf lima mike kilo"
    found = groups_of(cand(1, "2001-05-01 00:00:00", a), cand(2, "2001-05-02 00:00:00", b),
                      cand(3, "2001-05-03 00:00:00", c))
    assert found == [("<1@x>", ["<2@x>"])]  # C starts its own (single-email) group


def test_equal_timestamps_break_ties_by_source_file():
    found = groups_of(cand(2), cand(1))
    assert found == [("<1@x>", ["<2@x>"])]  # maildir/a/inbox/1. sorts first


# --- database integration ----------------------------------------------------

def parsed(n: int, day: int, body: str = BODY) -> ParsedEmail:
    return ParsedEmail(
        message_id=f"<{n}@x>", date=datetime(2001, 5, day, tzinfo=timezone.utc),
        from_address="a@enron.com", to_addresses=["b@enron.com"], subject="Budget",
        body=body, source_file=f"maildir/a/inbox/{n}.",
    )


@pytest.fixture
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_schema(conn, SCHEMA_PATH)
    db.load_emails(conn, [parsed(1, 1), parsed(2, 2), parsed(3, 3), parsed(4, 4, "Different email.")])
    yield conn
    conn.close()


def run_dedupe(conn):
    groups = find_duplicate_groups(dedupe.load_candidates(conn), 90.0)
    dedupe.apply_flags(conn, groups)
    return groups


def test_flags_are_written_to_the_database(conn):
    run_dedupe(conn)
    rows = {r["message_id"]: r for r in conn.execute("SELECT * FROM emails")}
    assert rows["<1@x>"]["is_duplicate"] == 0
    assert rows["<4@x>"]["is_duplicate"] == 0
    for mid in ("<2@x>", "<3@x>"):
        assert rows[mid]["is_duplicate"] == 1
        assert rows[mid]["duplicate_of"] == "<1@x>"
        assert rows[mid]["similarity_score"] == 100.0


def test_rescan_is_idempotent_and_keeps_notification_state(conn):
    run_dedupe(conn)
    conn.execute("UPDATE emails SET notification_sent = 1, notification_date = '2026-01-01 00:00:00' "
                 "WHERE message_id = '<3@x>'")
    run_dedupe(conn)
    assert conn.execute("SELECT COUNT(*) FROM emails WHERE is_duplicate = 1").fetchone()[0] == 2
    assert conn.execute("SELECT notification_sent FROM emails WHERE message_id = '<3@x>'").fetchone()[0] == 1


def test_report_and_summary(conn, tmp_path):
    groups = run_dedupe(conn)
    report = tmp_path / "report.csv"
    dedupe.write_report(groups, report)
    with report.open() as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0]) == list(dedupe.REPORT_COLUMNS)
    assert [(r["duplicate_message_id"], r["original_message_id"]) for r in rows] == [
        ("<2@x>", "<1@x>"), ("<3@x>", "<1@x>"),
    ]
    assert rows[1]["duplicate_date"] == "2001-05-03 00:00:00"
    assert rows[1]["original_date"] == "2001-05-01 00:00:00"
    assert rows[1]["similarity_score"] == "100.00"

    stats = dedupe.summarize(groups, emails_scanned=4, threshold=90.0)
    assert stats["groups"] == 1
    assert stats["emails_flagged"] == 2
    assert stats["average_group_size"] == 3.0
