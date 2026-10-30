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
    assert email.body == "Hello Ken."
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
    assert email.body == "It’s done"  # 0x92 is a cp1252 curly apostrophe


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


def test_optional_fields_absent_are_empty():
    email = parse(make_email())
    assert email.cc_addresses == [] and email.bcc_addresses == []
    assert email.x_from is None and email.x_folder is None
    assert email.content_type is None
    assert email.has_attachment is False
    assert email.forwarded_content is None and email.quoted_content is None
    assert email.headings == []


def test_optional_enron_headers():
    raw = make_email(
        Cc="x@enron.com,\n\ty@enron.com", Bcc="z@enron.com",
        X_From="Ortiz, Lucy </O=ENRON/OU=NA/CN=RECIPIENTS/CN=LORTIZ>",
        X_To="Lay, Kenneth", X_cc="", X_bcc="",
        X_Folder="\\KLAY (Non-Privileged)\\Lay, Kenneth\\Inbox", X_Origin="Lay-K",
        Content_Type="text/plain; charset=us-ascii",
    )
    email = parse(raw)
    assert email.cc_addresses == ["x@enron.com", "y@enron.com"]
    assert email.bcc_addresses == ["z@enron.com"]
    assert email.x_from == "Ortiz, Lucy"  # display name only
    assert email.x_to == "Lay, Kenneth"
    assert email.x_cc is None and email.x_bcc is None  # empty header -> NULL
    assert email.x_folder == "\\KLAY (Non-Privileged)\\Lay, Kenneth\\Inbox"
    assert email.x_origin == "Lay-K"
    assert email.content_type == "text/plain"


def test_bcc_is_captured_as_written_even_when_it_equals_cc():
    email = parse(make_email(Cc="x@enron.com, y@enron.com", Bcc="x@enron.com, y@enron.com"))
    assert email.cc_addresses == ["x@enron.com", "y@enron.com"]
    assert email.bcc_addresses == ["x@enron.com", "y@enron.com"]


# --- regressions from code review --------------------------------------------

def test_encoded_line_break_in_subject_is_neutralized():
    email = parse(make_email(Subject="=?utf-8?q?Budget=0ABcc:_spy@x.com?="))
    assert "\n" not in email.subject and "\r" not in email.subject
    assert email.subject == "Budget Bcc: spy@x.com"


def test_am_pm_date_with_rfc_like_prefix_is_not_misread():
    assert parse(make_email(Date="13 Nov 2001 4:39 PM CDT")).date == datetime(
        2001, 11, 13, 21, 39, tzinfo=timezone.utc
    )


@pytest.mark.parametrize("header", ["May 2001", "Nov 13", "4:39 PM CDT"])
def test_incomplete_date_is_rejected_not_guessed(header):
    with pytest.raises(ParseError, match="incomplete Date"):
        parse(make_email(Date=header))


@pytest.mark.parametrize("header, field", [("From", "From address"), ("To", "To addresses")])
def test_non_address_token_is_not_a_valid_mandatory_address(header, field):
    with pytest.raises(ParseError, match=field):
        parse(make_email(**{header: "garbage"}))


def test_invalid_entries_are_dropped_from_mixed_lists():
    assert parse(make_email(To="nonsense, good@enron.com")).to_addresses == ["good@enron.com"]


def test_body_split_and_headings_are_wired_in():
    body = (
        "AGENDA\n\nSee below.\n> quoted line\n"
        "-----Original Message-----\nFrom: a\n\nMARKET UPDATE\nprices up\n"
    )
    email = parse(make_email(body=body))
    assert email.body == "AGENDA\n\nSee below."
    assert email.quoted_content == "> quoted line"
    assert email.forwarded_content.startswith("-----Original Message-----")
    assert email.headings == ["AGENDA", "MARKET UPDATE"]


def test_attachment_detected_from_mime_part():
    body = (
        "--XYZ\nContent-Type: text/plain\n\nSee attached\n"
        "--XYZ\nContent-Type: application/pdf\nContent-Disposition: attachment; filename=a.pdf\n\nPDF\n"
        "--XYZ--\n"
    )
    raw = make_email(body=body, Content_Type='multipart/mixed; boundary="XYZ"')
    assert parse(raw).has_attachment is True


def test_attachment_detected_from_body_reference():
    assert parse(make_email(body="Here it is.\n - Budget.xls\n")).has_attachment is True


@pytest.mark.parametrize("header, expected_hour", [
    ("13 Nov 2001 4:39 PM", 16),        # RFC-like prefix, AM/PM, no zone
    ("13 Nov 2001 4:39 am", 4),
])
def test_am_pm_is_not_mistaken_for_a_timezone(header, expected_hour):
    assert parse(make_email(Date=header)).date.hour == expected_hour


@pytest.mark.parametrize("header", ["Mon, 14 May 2001 16:39:00 XYZ", "Monday, May 14, 2001 4:39 PM XYZ"])
def test_unknown_timezone_is_rejected_not_assumed_utc(header):
    with pytest.raises(ParseError, match="unknown timezone"):
        parse(make_email(Date=header))


def html_only(html: str, content_type: str = "multipart/alternative") -> bytes:
    if content_type == "text/html":
        return make_email(body=html, Content_Type="text/html; charset=us-ascii")
    body = f"--B\nContent-Type: text/html\n\n{html}\n--B--\n"
    return make_email(body=body, Content_Type=f'{content_type}; boundary="B"')


def test_html_only_multipart_body_is_converted_to_text():
    email = parse(html_only("<p>Quarterly numbers&nbsp;attached</p><p>Thanks, Ken</p>"))
    assert email.body == "Quarterly numbers attached\n\nThanks, Ken"  # &nbsp; -> space


def test_single_part_html_body_is_converted_to_text():
    assert parse(html_only("<div>Hello <b>Ken</b></div>", "text/html")).body == "Hello Ken"


def test_different_html_emails_get_different_bodies():
    # Regression: both used to become body="" and then matched as duplicates.
    first = parse(html_only("<p>Quarterly numbers attached</p>")).body
    second = parse(html_only("<p>Lunch on Friday?</p>")).body
    assert first and second and first != second


def test_plain_text_is_preferred_over_html():
    body = ("--B\nContent-Type: text/plain\n\nPlain version\n"
            "--B\nContent-Type: text/html\n\n<p>HTML version</p>\n--B--\n")
    email = parse(make_email(body=body, Content_Type='multipart/alternative; boundary="B"'))
    assert email.body == "Plain version"


def test_attachment_without_filename_is_not_the_body():
    body = ("--B\nContent-Type: text/html\n\n<p>Real message</p>\n"
            "--B\nContent-Type: text/plain\nContent-Disposition: attachment\n\nATTACHED FILE TEXT\n--B--\n")
    email = parse(make_email(body=body, Content_Type='multipart/mixed; boundary="B"'))
    assert email.body == "Real message"
    assert email.has_attachment is True


@pytest.mark.parametrize("header", ["Monday, May 14, 2001 4:39 PM xyz", "Monday, May 14, 2001 4:39 PM Xyz"])
def test_unknown_timezone_is_rejected_in_any_case(header):
    with pytest.raises(ParseError, match="unknown timezone|unparseable Date"):
        parse(make_email(Date=header))


@pytest.mark.parametrize("raw, expected", [
    ("Ortiz, Lucy </O=ENRON/OU=NA/CN=RECIPIENTS/CN=LORTIZ>", "Ortiz, Lucy"),
    ('"Garten, Jeffrey" <jeffrey.garten@yale.edu>', "Garten, Jeffrey"),
    ("Rieker, Paula </O=ENRON/CN=Prieker>, Valencia, Laura </O=ENRON/CN=Lvalenc>", "Rieker, Paula; Valencia, Laura"),
    ("Lay, Kenneth", "Lay, Kenneth"),
    ("<only@address.com>", None),
])
def test_x_headers_keep_display_names_only(raw, expected):
    assert parse(make_email(X_To=raw)).x_to == expected
