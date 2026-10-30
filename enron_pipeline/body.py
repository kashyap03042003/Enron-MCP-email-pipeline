"""Analyse email body text: split out forwarded/quoted content, find headings,
and spot references to attachments.

All functions here are pure text -> value transforms so they can be tested
without building full email messages.
"""

import re
from dataclasses import dataclass

# Lines that open an embedded (forwarded or replied-to) message.
_EMBEDDED_START = re.compile(
    r"""^\s*(?:
        -{2,}\s*Original\ Message\s*-{2,}                 # Outlook
      | -{2,}\s*Forwarded\ by\ .*                         # Lotus Notes
      | -{2,}\s*Forwarded\ message\s*-{2,}                # Gmail-style
      | Begin\ forwarded\ message:                        # Apple Mail
      | From:\s.+\son\s\d{1,2}/\d{1,2}/\d{2,4}\b.*        # Notes inline reply header
    )\s*$""",
    re.IGNORECASE | re.VERBOSE,
)
_QUOTE_ATTRIBUTION = re.compile(r"^\s*On .+ wrote:\s*$")

_HEADER_LABEL = re.compile(
    r"^(from|to|cc|bcc|sent|date|subject|importance|re|fw|fwd)\s*:", re.IGNORECASE
)
_LABEL_VALUE = re.compile(r"^[^:]{1,30}:\s+\S")  # "Shipping: $12.65"
_MARKER_TEXT = re.compile(r"^(forwarded by|original message)\b", re.IGNORECASE)
_NOT_HEADINGS = {"FYI", "ASAP", "NOTE", "THANKS", "URGENT", "IMPORTANT"}
_GREETING = re.compile(
    r"^(dear|hi|hello|hey|thanks|thank you|regards|best regards|sincerely|cheers)\b",
    re.IGNORECASE,
)
_UNDERLINE = re.compile(r"[=\-_*~]{3,}")
_DECORATION = "+*=-_#~ \t"
# "3. Market Outlook", "IV. Wrap Up", "A. The California Markets". Lettered
# items need 3+ words so initials like "J. D. Smith" are not mistaken for one.
_NUMBERED_TITLE = re.compile(r"^(?:(?:\d{1,2}|[IVX]{1,4})[.)]\s+[A-Z]|[A-Z][.)]\s+[A-Z]\S*(?:\s+\S+){2,})")
_SENTENCE_END = (".", ",", ";", "!", "?")

_ATTACHMENT_EXT = (
    r"(?:docx?|xlsx?|pptx?|pdf|txt|rtf|csv|zip|vcf|jpe?g|gif|png|bmp|tiff?"
    r"|html?|wpd|msg|mpp|dat)"
)
# Outlook placeholder "<<Budget.xls>>", or the " - Budget.xls" lines Enron's
# export left where attachments were stripped.
_ATTACHMENT_REF = re.compile(
    rf"<<[^<>\n]+\.{_ATTACHMENT_EXT}>>|^\s*-\s+\S[^\n]*\.{_ATTACHMENT_EXT}\s*$",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass
class BodyParts:
    body: str
    forwarded_content: str | None
    quoted_content: str | None


def split_body(text: str) -> BodyParts:
    """Separate the author's own text from embedded and quoted content.

    Everything from the first embedded-message marker onward is the
    forwarded content (kept whole, including any nested forwards). In the
    remaining text, '>'-prefixed lines (plus an "On ... wrote:" line that
    introduces them) are the quoted content.
    """
    lines = text.splitlines()
    split_at = next((i for i, line in enumerate(lines) if _EMBEDDED_START.match(line)), len(lines))
    own, embedded = lines[:split_at], lines[split_at:]

    body, quoted = [], []
    for i, line in enumerate(own):
        next_line = own[i + 1] if i + 1 < len(own) else ""
        if _is_quoted(line) or (_QUOTE_ATTRIBUTION.match(line) and _is_quoted(next_line)):
            quoted.append(line)
        else:
            body.append(line)

    return BodyParts(
        body="\n".join(body).strip(),
        forwarded_content=_none_if_blank("\n".join(embedded)),
        quoted_content=_none_if_blank("\n".join(quoted)),
    )


def extract_headings(text: str) -> list[str]:
    """Return heading-like lines in order of appearance, without repeats.

    A heading is a short line, not ending like a sentence, that is either
    underlined (===, ---) or, when preceded by a blank line, written in
    ALL CAPS or numbered like a section title ("3. Market Outlook").
    Numbered and underlined lines must also be in Title Case, which tells
    "2. Prospects for a Bailout" apart from a wrapped list sentence.
    """
    lines = text.splitlines()
    # In an email typed entirely in capitals, capitalisation means nothing.
    # Judge that from sentence-like lines only, so headings don't count.
    prose = "\n".join(line for line in lines if len(line.split()) >= 4)
    shouting = _upper_ratio(prose) > 0.5

    found: dict[str, None] = {}
    for i, raw in enumerate(lines):
        line = raw.strip()
        candidate = line.strip(_DECORATION)
        if not _plausible_heading(candidate):
            continue
        prev_blank = i == 0 or not lines[i - 1].strip()
        next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
        underlined = bool(_UNDERLINE.fullmatch(next_line)) and not _UNDERLINE.fullmatch(line)

        all_caps = not shouting and _is_all_caps(candidate)
        numbered = bool(_NUMBERED_TITLE.match(candidate))
        if all_caps and (prev_blank or underlined):
            found.setdefault(candidate)
        elif (underlined or (prev_blank and numbered)) and _is_title_case(candidate):
            found.setdefault(candidate)
    return list(found)


def mentions_attachment(text: str) -> bool:
    return bool(_ATTACHMENT_REF.search(text))


def _plausible_heading(line: str) -> bool:
    return (
        3 <= len(line) <= 80
        and len(line.split()) <= 10
        and not line.endswith(_SENTENCE_END + (":",))
        and line[0] not in "[<>(\"'"
        and not any(token in line.lower() for token in ("@", "http", "www."))
        and not _HEADER_LABEL.match(line)
        and not _LABEL_VALUE.match(line)
        and not _MARKER_TEXT.match(line)
        and not _GREETING.match(line)
        and line.upper() not in _NOT_HEADINGS
    )


def _is_all_caps(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    return line[0].isalpha() and len(letters) >= 4 and all(c.isupper() for c in letters)


def _is_title_case(line: str) -> bool:
    # Ignore short function words ("for", "a", "of") when judging capitalisation.
    words = [w for w in re.findall(r"[A-Za-z]+", line) if len(w) > 3]
    return bool(words) and sum(w[0].isupper() for w in words) / len(words) >= 0.6


def _upper_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    return sum(c.isupper() for c in letters) / len(letters) if letters else 0.0


def _is_quoted(line: str) -> bool:
    return line.lstrip().startswith(">")


def _none_if_blank(text: str) -> str | None:
    return text.strip() or None
