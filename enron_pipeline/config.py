"""Central configuration: paths and defaults shared by every pipeline stage."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAILDIR = PROJECT_ROOT / "maildir"

# Default subset: five mailboxes spanning executive, trading and legal roles
# (~23.6k emails). Override with `--mailboxes` on the CLI.
DEFAULT_MAILBOXES = (
    "lay-k",       # Kenneth Lay, Chairman/CEO
    "skilling-j",  # Jeff Skilling, CEO
    "delainey-d",  # David Delainey, CEO Enron Energy Services
    "lavorato-j",  # John Lavorato, head of trading
    "haedicke-m",  # Mark Haedicke, General Counsel
)
