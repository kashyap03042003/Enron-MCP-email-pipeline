"""Parse one raw RFC 2822 Enron email file into a ParsedEmail.

Built on the stdlib `email` package with the lenient compat32 policy, plus
fixes for quirks found in the Enron data:
  * headers may contain raw 8-bit bytes (not valid UTF-8);
  * one malformed entry makes `getaddresses` drop the whole address list;
  * some Date headers carry only a zone abbreviation, or no zone at all.
"""

import re
from datetime import datetime, timezone
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import Message
from email.policy import compat32
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

from dateutil import parser as date_parser

from .models import ParsedEmail, ParseError

# Zone abbreviations for the dateutil fallback (offsets in seconds).
TZ_ABBREVIATIONS = {
    "UT": 0, "UTC": 0, "GMT": 0,
    "EST": -5 * 3600, "EDT": -4 * 3600,
    "CST": -6 * 3600, "CDT": -5 * 3600,
    "MST": -7 * 3600, "MDT": -6 * 3600,
    "PST": -8 * 3600, "PDT": -7 * 3600,
}

_FOLDING = re.compile(r"\s*\r?\n\s+")
_ADDR_SPEC = re.compile(r"[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+")
# "[Tue, ]13 Nov 2001 18:25[:59]" — the stdlib parser silently misreads other
# layouts (e.g. it ignores "PM"), so anything else goes to dateutil.
_RFC2822_DATE = re.compile(r"^\s*(?:[A-Za-z]{3},\s*)?\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4}\s+\d{1,2}:\d{2}")


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

    return ParsedEmail(
        message_id=message_id,
        date=_parse_date(_header(msg, "Date")),
        from_address=from_addresses[0],
        to_addresses=to_addresses,
        subject=subject,
        body=_body(msg),
        source_file=source_file,
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
    return _FOLDING.sub(" ", value).strip()


def _addresses(msg: Message, name: str) -> list[str]:
    """All addresses in every occurrence of a header: lowercased, de-duplicated, in order."""
    found: dict[str, None] = {}
    for value in _raw_headers(msg, name):
        value = _clean_header(value)
        parsed = [addr for _, addr in getaddresses([value]) if addr]
        if not parsed:
            # Strict getaddresses returns nothing if any one entry is malformed;
            # fall back to pulling out anything shaped like an address.
            parsed = _ADDR_SPEC.findall(value)
        for addr in parsed:
            addr = addr.strip().strip("'\"").lower()
            if addr:
                found.setdefault(addr)
    return list(found)


def _parse_date(value: str | None) -> datetime:
    if not value:
        raise ParseError("missing Date header")
    dt = None
    if _RFC2822_DATE.match(value):
        try:
            dt = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            pass
    if dt is None:
        try:
            dt = date_parser.parse(value, tzinfos=TZ_ABBREVIATIONS, fuzzy=True)
        except (ValueError, OverflowError) as exc:
            raise ParseError(f"unparseable Date header: {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)  # no zone information: assume UTC
    return dt.astimezone(timezone.utc)


# --- body ------------------------------------------------------------------

def _body(msg: Message) -> str:
    if msg.is_multipart():
        parts = [
            p for p in msg.walk()
            if p.get_content_type() == "text/plain" and not p.get_filename()
        ]
        text = "\n".join(_payload_text(p) for p in parts)
    else:
        text = _payload_text(msg)
    return text.replace("\r\n", "\n")


def _payload_text(part: Message) -> str:
    payload = part.get_payload(decode=True)  # undoes base64 / quoted-printable
    if payload is None:
        return ""
    return decode_bytes(payload, part.get_content_charset())


def decode_bytes(data: bytes, charset: str | None = None) -> str:
    """Decode with the declared charset, then UTF-8, then cp1252; latin-1 never fails."""
    for encoding in (charset, "utf-8", "cp1252"):
        if not encoding:
            continue
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("latin-1")
