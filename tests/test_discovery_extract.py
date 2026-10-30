from enron_pipeline.discovery import discover_emails
from enron_pipeline.extract import extract

VALID = (
    b"Message-ID: <1@x>\nDate: Mon, 14 May 2001 16:39:00 -0700\n"
    b"From: a@enron.com\nTo: b@enron.com\nSubject: hi\n\nbody\n"
)


def make_maildir(tmp_path):
    maildir = tmp_path / "maildir"
    (maildir / "lay-k" / "inbox" / "nested").mkdir(parents=True)
    (maildir / "lay-k" / "inbox" / "1.").write_bytes(VALID)
    (maildir / "lay-k" / "inbox" / "nested" / "2.").write_bytes(VALID)
    (maildir / "lay-k" / ".DS_Store").write_bytes(b"\x00junk")
    (maildir / "skilling-j" / "sent").mkdir(parents=True)
    (maildir / "skilling-j" / "sent" / "1.").write_bytes(b"garbage with no headers")
    return maildir


def test_discovery_recurses_and_skips_hidden_files(tmp_path):
    maildir = make_maildir(tmp_path)
    found = [p.relative_to(maildir).as_posix() for p in discover_emails(maildir)]
    assert found == ["lay-k/inbox/1.", "lay-k/inbox/nested/2.", "skilling-j/sent/1."]


def test_discovery_filters_mailboxes(tmp_path):
    maildir = make_maildir(tmp_path)
    assert len(list(discover_emails(maildir, ["skilling-j"]))) == 1


def test_extract_collects_failures_without_crashing(tmp_path):
    maildir = make_maildir(tmp_path)
    result = extract(discover_emails(maildir), tmp_path)
    assert result.total_files == 3
    assert len(result.emails) == 2
    assert result.failures == [("maildir/skilling-j/sent/1.", "missing Message-ID header")]
    assert result.emails[0].source_file == "maildir/lay-k/inbox/1."
