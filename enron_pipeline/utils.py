"""Small generic helpers shared across the pipeline.

Rule for this module: a function belongs here only if it knows nothing about
emails, Enron or the database schema. Domain logic stays in its own module.
"""

import re
from datetime import datetime, timezone
from html.parser import HTMLParser

# --- text --------------------------------------------------------------------

def collapse_whitespace(*parts: str | None) -> str:
    """Join the non-empty parts and squeeze every run of whitespace to one space."""
    return " ".join(" ".join(p for p in parts if p).split())


def none_if_blank(text: str | None) -> str | None:
    """'' or whitespace-only -> None; anything else is returned stripped."""
    return (text or "").strip() or None


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


def html_to_text(html: str) -> str:
    """Readable plain text from HTML: tags removed, entities decoded, block
    elements on their own lines, script/style content dropped."""
    extractor = _TextExtractor()
    extractor.feed(html)
    extractor.close()
    lines = (" ".join(line.split()) for line in "".join(extractor.parts).splitlines())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


class _TextExtractor(HTMLParser):
    _BLOCKS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "blockquote"}

    def __init__(self):
        super().__init__()  # convert_charrefs=True decodes &amp; etc.
        self.parts: list[str] = []
        self._skip = 0  # depth inside <script>/<style>

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def safe_filename(name: str) -> str:
    """Make a string usable as a file name: '<a/b@x>' -> 'a_b@x'."""
    return re.sub(r"[^\w.@-]", "_", name.strip("<>"))


# --- time --------------------------------------------------------------------

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_db_datetime(dt: datetime) -> str:
    """Aware datetime -> 'YYYY-MM-DD HH:MM:SS' in UTC (sortable as text)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
