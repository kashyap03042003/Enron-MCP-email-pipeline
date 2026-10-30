"""Central configuration: paths and defaults shared by every pipeline stage."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAILDIR = PROJECT_ROOT / "maildir"
OUTPUT_DIR = PROJECT_ROOT / "output"
ERROR_LOG = PROJECT_ROOT / "error_log.txt"
EXTRACTION_STATS = OUTPUT_DIR / "extraction_stats.json"
SCHEMA_PATH = PROJECT_ROOT / "schema.sql"
DB_PATH = PROJECT_ROOT / "enron.db"

# Default subset: five mailboxes spanning executive, trading and legal roles
# (~23.6k emails). Override with `--mailboxes` on the CLI.
DEFAULT_MAILBOXES = (
    "lay-k",       # Kenneth Lay, Chairman/CEO
    "skilling-j",  # Jeff Skilling, CEO
    "delainey-d",  # David Delainey, CEO Enron Energy Services
    "lavorato-j",  # John Lavorato, head of trading
    "haedicke-m",  # Mark Haedicke, General Counsel
)

# Duplicate detection (Task 3)
SIMILARITY_THRESHOLD = 90.0
DUPLICATES_REPORT = PROJECT_ROOT / "duplicates_report.csv"
DEDUPE_STATS = OUTPUT_DIR / "dedupe_stats.json"
