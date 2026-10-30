"""Parse one raw RFC 2822 Enron email file into a ParsedEmail.

Built on the stdlib `email` package with the lenient compat32 policy, plus
fixes for quirks found in the Enron data:
  * headers may contain raw 8-bit bytes (not valid UTF-8);
  * one malformed entry makes `getaddresses` drop the whole address list;
  * some Date headers carry only a zone abbreviation, or no zone at all.
"""

import re
import warnings
from datetime import datetime, timezone
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import Message
from email.policy import compat32
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

from dateutil import parser as date_parser
from dateutil.parser import UnknownTimezoneWarning

from .body import extract_headings, mentions_attachment, split_body
from .models import ParsedEmail, ParseError
from .utils import decode_bytes, html_to_text

# Zone abbreviations for the dateutil fallback (offsets in seconds).
TZ_ABBREVIATIONS = {
    "UT": 0, "UTC": 0, "GMT": 0, "Z": 0,
    "EST": -5 * 3600, "EDT": -4 * 3600,
    "CST": -6 * 3600, "CDT": -5 * 3600,
    "MST": -7 * 3600, "MDT": -6 * 3600,
    "PST": -8 * 3600, "PDT": -7 * 3600,
}

_FOLDING = re.compile(r"\s*\r?\n\s+")
_ADDR_SPEC = re.compile(r"[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+")
_VALID_ADDRESS = re.compile(r"^[^@\s<>(),;:\"\[\]]+@[^@\s<>(),;:\"\[\]]+$")
# Line breaks and other control characters (tab excepted) left after decoding.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]+")
# A complete RFC 2822 date: "[Tue, ]13 Nov 2001 18:25[:59] [-0800|PST] [(PST)]".
# The stdlib parser silently misreads anything else (e.g. it ignores "PM"),
# so other layouts go to dateutil.
# AM/PM is not a zone: "4:39 PM" must go to dateutil.
_RFC2822_DATE = re.compile(
    r"^\s*(?:[A-Za-z]{3},\s*)?\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4}\s+\d{1,2}:\d{2}(?::\d{2})?"
    r"\s*(?P<zone>[+-]\d{4}|(?![AaPp][Mm]\b)[A-Za-z]{1,5})?\s*(?:\([^)]*\))?\s*$"
)


def parse_file(path: Path, project_root: Path) -> ParsedEmail:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ParseError(f"unreadable file: {exc}") from exc
    return parse_bytes(raw, source_file=path.relative_to(project_root).as_posix())


def parse_bytes(raw: bytes, source_file: str) -> ParsedEmail:
    msg = message_from_bytes(raw, policy=compat32)

    message_id = _header(msg, "Message-ID")
    if not message_id:
        raise ParseError("missing Message-ID header")

    from_addresses = _addresses(msg, "From")
    if not from_addresses:
        raise ParseError("missing or unparseable From address")

    to_addresses = _addresses(msg, "To")
    if not to_addresses:
        raise ParseError("missing or unparseable To addresses")

    # An empty Subject header is a valid (extracted) empty subject; only a
    # missing header counts as a failure.
    subject = _header(msg, "Subject")
    if subject is None:
        raise ParseError("missing Subject header")

    text = _body(msg)
    parts = split_body(text)
    return ParsedEmail(
        message_id=message_id,
        date=_parse_date(_header(msg, "Date")),
        from_address=from_addresses[0],
        to_addresses=to_addresses,
        subject=subject,
        body=parts.body,
        source_file=source_file,
        cc_addresses=_addresses(msg, "Cc"),
        bcc_addresses=_addresses(msg, "Bcc"),
        x_from=_header(msg, "X-From") or None,
        x_to=_header(msg, "X-To") or None,
        x_cc=_header(msg, "X-cc") or None,
        x_bcc=_header(msg, "X-bcc") or None,
        x_folder=_header(msg, "X-Folder") or None,
        x_origin=_header(msg, "X-Origin") or None,
        content_type=msg.get_content_type() if _raw_headers(msg, "Content-Type") else None,
        has_attachment=_has_mime_attachment(msg) or mentions_attachment(text),
        forwarded_content=parts.forwarded_content,
        quoted_content=parts.quoted_content,
        headings=extract_headings("\n\n".join(filter(None, (parts.body, parts.forwarded_content)))),
    )


# --- headers ---------------------------------------------------------------

def _raw_headers(msg: Message, name: str) -> list[str]:
    name = name.lower()
    return [value for key, value in msg.raw_items() if key.lower() == name]


def _header(msg: Message, name: str) -> str | None:
    """First occurrence of a header as clean text, or None if absent."""
    values = _raw_headers(msg, name)
    return _clean_header(values[0]) if values else None


def _clean_header(value: str) -> str:
    # The bytes parser smuggles undecodable bytes through as surrogates;
    # recover the original bytes and decode them properly.
    value = decode_bytes(value.encode("ascii", "surrogateescape"))
    if "=?" in value:  # RFC 2047 encoded-words, e.g. =?iso-8859-1?q?...?=
        try:
            value = str(make_header(decode_header(value)))
        except (ValueError, LookupError):
            pass
    value = _FOLDING.sub(" ", value)
    # A decoded encoded-word can still contain a line break, which is not
    # allowed in a header we later write (drafts, notices).
    return _CONTROL_CHARS.sub(" ", value).strip()


def _addresses(msg: Message, name: str) -> list[str]:
    """All valid addresses in every occurrence of a header: lowercased,
    de-duplicated, in order. Tokens that are not user@domain are dropped."""
    found: dict[str, None] = {}
    for value in _raw_headers(msg, name):
        value = _clean_header(value)
        parsed = [_normalize_address(addr) for _, addr in getaddresses([value])]
        parsed = [addr for addr in parsed if _VALID_ADDRESS.match(addr)]
        if not parsed:
            # Strict getaddresses returns nothing if any one entry is malformed;
            # fall back to pulling out anything shaped like an address.
            parsed = [_normalize_address(addr) for addr in _ADDR_SPEC.findall(value)]
        for addr in parsed:
            found.setdefault(addr)
    return list(found)


def _normalize_address(addr: str) -> str:
    return addr.strip().strip("'\"").lower()


def _parse_date(value: str | None) -> datetime:
    if not value:
        raise ParseError("missing Date header")
    dt = None
    if rfc := _RFC2822_DATE.match(value):
        zone = rfc["zone"]
        if zone and zone.isalpha() and zone.upper() not in TZ_ABBREVIATIONS:
            raise ParseError(f"unknown timezone in Date header: {value!r}")
        try:
            dt = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            pass
    if dt is None:
        dt = _parse_date_fallback(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)  # no zone information: assume UTC
    return dt.astimezone(timezone.utc)


def _parse_date_fallback(value: str) -> datetime:
    """dateutil for non-RFC layouts. It fills missing parts from a default
    date, so parse with two different defaults: if the results differ, the
    header lacks a year, month or day and is rejected rather than guessed."""
    try:
        with warnings.catch_warnings():
            # A zone name dateutil doesn't know would otherwise be dropped
            # silently and the time read as UTC.
            warnings.simplefilter("error", UnknownTimezoneWarning)
            first, second = (
                date_parser.parse(value, tzinfos=TZ_ABBREVIATIONS, fuzzy=True, default=default)
                for default in (datetime(1901, 1, 1), datetime(1902, 2, 2))
            )
    except UnknownTimezoneWarning as exc:
        raise ParseError(f"unknown timezone in Date header: {value!r}") from exc
    except (ValueError, OverflowError) as exc:
        raise ParseError(f"unparseable Date header: {value!r}") from exc
    if first != second:
        raise ParseError(f"incomplete Date header: {value!r}")
    return first


# --- body ------------------------------------------------------------------

def _body(msg: Message) -> str:
    """The message text: its text/plain parts, or, when there are none, its
    text/html parts converted to text. Attachments are never included."""
    inline = [p for p in msg.walk() if not p.is_multipart() and not p.get_filename()]
    plain = [p for p in inline if p.get_content_type() == "text/plain"]
    if plain:
        text = "\n".join(_payload_text(p) for p in plain)
    else:
        html = [p for p in inline if p.get_content_type() == "text/html"]
        text = "\n".join(html_to_text(_payload_text(p)) for p in html)
    return text.replace("\r\n", "\n")


def _has_mime_attachment(msg: Message) -> bool:
    return any(
        part.get_filename() or (part.get("Content-Disposition") or "").lower().startswith("attachment")
        for part in msg.walk()
        if not part.is_multipart()
    )


def _payload_text(part: Message) -> str:
    payload = part.get_payload(decode=True)  # undoes base64 / quoted-printable
    if payload is None:
        return ""
    return decode_bytes(payload, part.get_content_charset())
