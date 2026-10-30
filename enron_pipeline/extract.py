"""Stage 1: discover and parse email files, collecting successes and failures."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .models import ParsedEmail, ParseError
from .parser import parse_file


@dataclass
class ExtractionResult:
    emails: list[ParsedEmail] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)  # (source_file, reason)

    @property
    def total_files(self) -> int:
        return len(self.emails) + len(self.failures)


def extract(paths: Iterable[Path], project_root: Path) -> ExtractionResult:
    result = ExtractionResult()
    for path in paths:
        try:
            result.emails.append(parse_file(path, project_root))
        except ParseError as exc:
            result.failures.append((_relative(path, project_root), str(exc)))
        except Exception as exc:  # malformed input must never crash the pipeline
            reason = f"unexpected {type(exc).__name__}: {exc}"
            result.failures.append((_relative(path, project_root), reason))
    return result


def _relative(path: Path, project_root: Path) -> str:
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return str(path)
