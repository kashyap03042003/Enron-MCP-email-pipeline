from datetime import datetime, timezone

import pytest

from enron_pipeline.models import ParseError
from enron_pipeline.parser import parse_bytes

BASE_HEADERS = {
    "Message-ID": "<123.JavaMail.evans@thyme>",
    "Date": "Tue, 13 Nov 2001 18:25:59 -0800 (PST)",
    "From": "lucy.ortiz@enron.com",
    "To": "kenneth.lay@enron.com",
    "Subject": "Re: Thank you",
}


def make_email(body: str = "Hello Ken.\n", **overrides: str | None) -> bytes:
    """Build a raw email; pass Header_Name=None to drop a header."""
    headers = dict(BASE_HEADERS)
    for key, value in overrides.items():
        name = key.replace("_", "-")
        if value is None:
            headers.pop(name, None)
        else:
            headers[name] = value
    head = "".join(f"{k}: {v}\n" for k, v in headers.items())
    return (head + "\n" + body).encode("latin-1")


def parse(raw: bytes):
    return parse_bytes(raw, source_file="maildir/test/inbox/1.")


def test_extracts_mandatory_fields():
    email = parse(make_email())
    assert email.message_id == "<123.JavaMail.evans@thyme>"
    assert email.from_address == "lucy.ortiz@enron.com"
    assert email.to_addresses == ["kenneth.lay@enron.com"]
    assert email.subject == "Re: Thank you"  # Re: prefix preserved
    assert email.body == "Hello Ken.\n"
    assert email.source_file == "maildir/test/inbox/1."


def test_date_offset_is_normalized_to_utc():
    email = parse(make_email())
    assert email.date == datetime(2001, 11, 14, 2, 25, 59, tzinfo=timezone.utc)


@pytest.mark.parametrize("header, expected_hour", [
    ("Mon, 14 May 2001 16:39:00 PDT", 23),
    ("Mon, 14 May 2001 16:39:00 CDT", 21),
    ("14 May 2001 16:39 EST", 21),
    ("Monday, May 14, 2001 4:39 PM CDT", 21),  # non-RFC format -> dateutil fallback
    ("Mon, 14 May 2001 16:39:00", 16),         # no zone at all -> assumed UTC
])
def test_date_timezone_variants(header, expected_hour):
    email = parse(make_email(Date=header))
    assert email.date.tzinfo == timezone.utc
    assert email.date.hour == expected_hour


def test_folded_multi_recipient_to_header():
    raw = make_email(To="a@enron.com, B@Enron.com,\n\tc@enron.com, a@enron.com")
    assert parse(raw).to_addresses == ["a@enron.com", "b@enron.com", "c@enron.com"]


def test_display_names_are_stripped_from_addresses():
    raw = make_email(From='"Lay, Kenneth" <Kenneth.Lay@enron.com>',
                     To='"Doe, Jane" <jane@x.com>, bob@y.com')
    email = parse(raw)
    assert email.from_address == "kenneth.lay@enron.com"
    assert email.to_addresses == ["jane@x.com", "bob@y.com"]


def test_one_malformed_recipient_does_not_drop_the_others():
    email = parse(make_email(To="good@enron.com, <<broken, other@enron.com"))
    assert email.to_addresses == ["good@enron.com", "other@enron.com"]


def test_folded_subject_is_unfolded():
    assert parse(make_email(Subject="Long subject\n\tcontinued")).subject == "Long subject continued"


def test_empty_subject_is_accepted():
    assert parse(make_email(Subject="")).subject == ""


def test_non_ascii_bytes_in_headers_and_body():
    raw = make_email(Subject="Caf\xe9 meeting", body="It\x92s done\n")
    email = parse(raw)
    assert email.subject == "Café meeting"
    assert email.body == "It’s done\n"  # 0x92 is a cp1252 curly apostrophe


def test_encoded_word_subject_is_decoded():
    assert parse(make_email(Subject="=?iso-8859-1?q?Caf=E9?=")).subject == "Café"


def test_multipart_body_keeps_only_text_parts():
    body = (
        "--XYZ\nContent-Type: text/plain\n\nMain text\n"
        "--XYZ\nContent-Type: application/pdf; name=a.pdf\n"
        "Content-Disposition: attachment; filename=a.pdf\n\nPDFDATA\n"
        "--XYZ--\n"
    )
    raw = make_email(body=body, Content_Type='multipart/mixed; boundary="XYZ"')
    email = parse(raw)
    assert "Main text" in email.body
    assert "PDFDATA" not in email.body


@pytest.mark.parametrize("header, reason", [
    ("Message-ID", "missing Message-ID"),
    ("Date", "missing Date"),
    ("From", "From address"),
    ("To", "To addresses"),
    ("Subject", "missing Subject"),
])
def test_missing_mandatory_header_raises(header, reason):
    with pytest.raises(ParseError, match=reason):
        parse(make_email(**{header.replace("-", "_"): None}))


def test_unparseable_date_raises():
    with pytest.raises(ParseError, match="unparseable Date"):
        parse(make_email(Date="not a date at all"))


def test_undisclosed_recipients_counts_as_missing_to():
    with pytest.raises(ParseError, match="To addresses"):
        parse(make_email(To="undisclosed-recipients:;"))
