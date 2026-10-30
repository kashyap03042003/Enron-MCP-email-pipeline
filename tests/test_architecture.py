"""Single-responsibility rules, checked so they can't silently erode."""

import re

import pytest

from enron_pipeline.config import PROJECT_ROOT

SOURCES = sorted((PROJECT_ROOT / "enron_pipeline").glob("*.py")) + [PROJECT_ROOT / "main.py"]


def _modules_matching(pattern: str) -> set[str]:
    regex = re.compile(pattern)
    return {path.name for path in SOURCES if regex.search(path.read_text())}


def test_only_db_runs_sql():
    assert _modules_matching(r"\.execute(many|script)?\(") == {"db.py"}


def test_only_reports_writes_files():
    assert _modules_matching(r"\.write_(text|bytes)\(|\.open\(\s*\"[aw]") == {"reports.py"}


@pytest.mark.parametrize("module", ["dedupe.py", "body.py", "utils.py"])
def test_pure_modules_do_no_io(module):
    source = (PROJECT_ROOT / "enron_pipeline" / module).read_text()
    assert not re.search(r"\bsqlite3\b|\bopen\(|\.read_|\.write_", source)


def test_utils_has_no_project_imports():
    source = (PROJECT_ROOT / "enron_pipeline" / "utils.py").read_text()
    assert not re.search(r"^from \.", source, re.MULTILINE)
