from datetime import datetime, timedelta, timezone

import pytest

from enron_pipeline.utils import (
    collapse_whitespace, decode_bytes, none_if_blank, safe_filename, to_db_datetime, utc_now,
)


def test_collapse_whitespace_joins_non_empty_parts():
    assert collapse_whitespace("a  b\n\nc ", None, "", "\td") == "a b c d"
    assert collapse_whitespace() == ""


@pytest.mark.parametrize("text, expected", [(None, None), ("", None), ("  \n", None), (" hi ", "hi")])
def test_none_if_blank(text, expected):
    assert none_if_blank(text) == expected


@pytest.mark.parametrize("data, charset, expected", [
    ("café".encode("utf-8"), None, "café"),
    (b"It\x92s", None, "It’s"),           # not UTF-8 -> cp1252
    (b"caf\xe9", "us-ascii", "café"),       # wrong declared charset falls through
    (b"\x81", None, "\x81"),                # undefined in cp1252 -> latin-1
    (b"x", "no-such-charset", "x"),
])
def test_decode_bytes_never_fails(data, charset, expected):
    assert decode_bytes(data, charset) == expected


def test_safe_filename():
    assert safe_filename("<123.Java/Mail:x@thyme>") == "123.Java_Mail_x@thyme"


def test_time_helpers():
    assert utc_now().tzinfo == timezone.utc
    pst = timezone(timedelta(hours=-8))
    assert to_db_datetime(datetime(2001, 11, 13, 18, 25, 59, tzinfo=pst)) == "2001-11-14 02:25:59"
