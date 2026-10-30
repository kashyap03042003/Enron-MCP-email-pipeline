"""Find raw email files under maildir/<mailbox>/<folder>/.../<file>."""

import os
from collections.abc import Iterable, Iterator
from pathlib import Path


def discover_emails(maildir: Path, mailboxes: Iterable[str] | None = None) -> Iterator[Path]:
    """Yield every email file in the given mailboxes (all mailboxes if None).

    Folders can nest arbitrarily deep, so we walk recursively. Hidden files
    (e.g. macOS .DS_Store) are skipped. Output order is deterministic so that
    runs are reproducible.
    """
    if mailboxes is None:
        mailboxes = sorted(p.name for p in maildir.iterdir() if p.is_dir())

    for mailbox in mailboxes:
        root = maildir / mailbox
        if not root.is_dir():
            raise FileNotFoundError(f"Mailbox not found: {root}")
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for name in sorted(filenames):
                if not name.startswith("."):
                    yield Path(dirpath) / name
