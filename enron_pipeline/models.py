"""Typed records passed between pipeline stages."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ParsedEmail:
    # Mandatory fields
    message_id: str
    date: datetime  # timezone-aware, normalized to UTC
    from_address: str
    to_addresses: list[str]
    subject: str
    body: str
    source_file: str  # path relative to the project root, e.g. maildir/lay-k/inbox/1.

    # Optional fields: None / empty when not present
    cc_addresses: list[str] = field(default_factory=list)
    bcc_addresses: list[str] = field(default_factory=list)
    x_from: str | None = None
    x_to: str | None = None
    x_cc: str | None = None
    x_bcc: str | None = None
    x_folder: str | None = None
    x_origin: str | None = None
    content_type: str | None = None
    has_attachment: bool = False
    forwarded_content: str | None = None
    quoted_content: str | None = None
    headings: list[str] = field(default_factory=list)


MANDATORY_FIELDS = (
    "message_id", "date", "from_address", "to_addresses", "subject", "body", "source_file",
)
OPTIONAL_FIELDS = (
    "cc_addresses", "bcc_addresses", "x_from", "x_to", "x_cc", "x_bcc", "x_folder",
    "x_origin", "content_type", "has_attachment", "forwarded_content", "quoted_content",
    "headings",
)


class ParseError(Exception):
    """A file could not be turned into a ParsedEmail; the message is the logged reason."""
