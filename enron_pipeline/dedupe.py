"""Task 3: find and flag near-duplicate emails.

Two emails are duplicates when they have the same sender, the same subject
once Re:/Fw: prefixes are stripped, and content that is at least 90% similar
(rapidfuzz `fuzz.ratio`, a normalized edit-distance score from 0 to 100).

"Content" is checked twice: the full text (own body + quoted + forwarded)
must match, and so must the author's own body unless both bodies are empty.
The full-text check stops pure forwards (empty body) from matching each other
on nothing; the body check stops two different replies from matching just
because they quote the same long thread. The reported score is the
full-text similarity.

Emails are first grouped by (sender, normalized subject), which is part of
the definition and keeps comparisons within small groups. Inside each group,
emails are visited oldest first; each one joins the existing duplicate group
whose original (earliest email) it matches best at or above the threshold,
or else starts a new group. Every flagged duplicate is therefore at least
90% similar to the original it points to.

This module is pure logic: candidates in, groups out. Loading candidates and
saving flags live in db.py; the CSV report is written by reports.py.
"""

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from .utils import collapse_whitespace

_SUBJECT_PREFIX = re.compile(r"^\s*(?:(?:re|fwd?)\s*(?:\[\d+\])?\s*:\s*)+", re.IGNORECASE)


@dataclass(frozen=True)
class Candidate:
    message_id: str
    date: str  # UTC, 'YYYY-MM-DD HH:MM:SS' (sorts chronologically)
    from_address: str
    subject: str
    source_file: str
    body: str  # whitespace-normalized own text
    text: str  # whitespace-normalized full content (body + quoted + forwarded)


@dataclass
class DuplicateGroup:
    original: Candidate
    duplicates: list[tuple[Candidate, float]] = field(default_factory=list)  # oldest first

    @property
    def size(self) -> int:
        return 1 + len(self.duplicates)

    @property
    def latest(self) -> tuple[Candidate, float]:
        """The most recent duplicate: the one the sender is notified about."""
        return self.duplicates[-1]


def normalize_subject(subject: str) -> str:
    """'RE: Fw: Budget  Q3' -> 'budget q3'"""
    return " ".join(_SUBJECT_PREFIX.sub("", subject).split()).lower()


def normalize_text(*parts: str | None) -> str:
    """Join content parts and collapse whitespace, so copies of one email that
    differ only in line wrapping or trailing spaces compare as equal."""
    return collapse_whitespace(*parts)


def similarity(a: Candidate, b: Candidate, threshold: float) -> float:
    """Full-text similarity if the pair counts as duplicates, else 0."""
    # score_cutoff makes rapidfuzz return 0 below the threshold, and lets it
    # skip texts whose lengths are too different to ever reach it.
    score = fuzz.ratio(a.text, b.text, score_cutoff=threshold)
    if score and (a.body or b.body) and not fuzz.ratio(a.body, b.body, score_cutoff=threshold):
        return 0.0
    return score


def find_duplicate_groups(candidates: Iterable[Candidate], threshold: float) -> list[DuplicateGroup]:
    blocks: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
    for c in candidates:
        blocks[(c.from_address, normalize_subject(c.subject))].append(c)

    found = []
    for block in blocks.values():
        if len(block) < 2:
            continue
        block.sort(key=lambda c: (c.date, c.source_file))
        groups: list[DuplicateGroup] = []
        for candidate in block:
            best, best_score = None, 0.0
            for group in groups:
                score = similarity(group.original, candidate, threshold)
                if score > best_score:
                    best, best_score = group, score
            if best is None:
                groups.append(DuplicateGroup(original=candidate))
            else:
                best.duplicates.append((candidate, round(best_score, 2)))
        found.extend(g for g in groups if g.duplicates)

    found.sort(key=lambda g: (g.original.date, g.original.source_file))
    return found


def summarize(groups: list[DuplicateGroup], emails_scanned: int, threshold: float) -> dict:
    flagged = sum(len(g.duplicates) for g in groups)
    return {
        "emails_scanned": emails_scanned,
        "threshold": threshold,
        "groups": len(groups),
        "emails_flagged": flagged,
        "average_group_size": round(sum(g.size for g in groups) / len(groups), 2) if groups else 0.0,
        "largest_group": max((g.size for g in groups), default=0),
    }
