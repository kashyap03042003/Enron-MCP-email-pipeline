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

    # Optional fields (populated in later steps)
    cc_addresses: list[str] = field(default_factory=list)
    bcc_addresses: list[str] = field(default_factory=list)


class ParseError(Exception):
    """A file could not be turned into a ParsedEmail; the message is the logged reason."""
