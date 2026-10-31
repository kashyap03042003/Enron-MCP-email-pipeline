# Enron Email Pipeline

This pipeline turns raw Enron emails into a normalized SQLite database and
finds near-duplicate emails. It then generates a duplicate notice for each
group and delivers it to a configured test inbox through a **Gmail MCP
server**.

```
maildir/ ──► parse ──► SQLite ──► find duplicates ──► notify
             Task 1    Task 2     Task 3              Task 4
                                                      ├─ dry run:     .eml drafts
                                                      └─ --send-live: Gmail via MCP
```

```bash
python main.py                          # full pipeline, dry run: nothing is sent
python main.py --send-live --limit 1    # full pipeline, and send one notice
```

| | Result on the 5-mailbox subset |
|---|---|
| Files found | 23,573 |
| Parsed | 22,068 (the 1,505 failures are listed in `error_log.txt`) |
| Duplicate groups | 5,561 |
| Emails flagged | 10,627 (average group size 2.91; largest group 68) |
| Drafts written | 10,627, one per flagged email |
| Tests | 178, about 5 seconds |

**Contents:**
- [Setup](#setup)
- [Gmail MCP setup](#gmail-mcp-setup)
- [Usage](#usage)
- [Architecture](#architecture)
- [Mailbox selection](#mailbox-selection)
- [Design decisions](#design-decisions)
- [Known limitations](#known-limitations)
- [Outputs](#outputs)
- [How to verify](#how-to-verify)
- [Requirements checklist](#requirements-checklist)

---

## Setup

You need **Python 3.10 or newer**.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Dataset: about 1.7 GB unpacked. It is gitignored, so download it yourself.
curl -O https://www.cs.cmu.edu/~enron/enron_mail_20150507.tar.gz
tar -xzf enron_mail_20150507.tar.gz       # creates ./maildir

pytest -q
```

Every dependency is open source:

| Package | Used for |
|---|---|
| `python-dateutil` | Dates that aren't in RFC 2822 format |
| `rapidfuzz` | Fuzzy text matching |
| `mcp` | The official Model Context Protocol SDK, for both the server and the client |
| `python-dotenv` | Loading credentials from `.env` |
| `pytest` | Tests |

## Gmail MCP setup

You only need this for `--send-live`. Dry runs work without it.

The project includes its own MCP server,
[`mcp_server/gmail_server.py`](mcp_server/gmail_server.py). It provides a
`send_email` tool and sends through Gmail's SMTP server on port 465, using TLS
with certificate verification. It signs in with a **Gmail App Password**, so
there's no Google Cloud project or OAuth setup.

1. **Create an App Password.**
   - Use a personal Gmail account. School and work accounts often block App
     Passwords.
   - Turn on **2-Step Verification** under *Security* in your Google Account.
   - Create the password at <https://myaccount.google.com/apppasswords>.
   - **Your normal Google password will not work.**
2. **Add your credentials.** Both files are gitignored. Only the `.example`
   templates are committed.
   ```bash
   cp .env.example .env && chmod 600 .env       # set GMAIL_ADDRESS and GMAIL_APP_PASSWORD
   cp mcp_config.json.example mcp_config.json   # how the pipeline starts the server; placeholder credentials only
   ```
   **Then edit `.env`:** replace the placeholders with your Gmail address and
   the App Password from step 1. The copied files contain placeholders only,
   so sending fails until you do this. Credentials are looked up in this
   order: variables exported in your shell, then `.env`, then the placeholder
   `env` block in `mcp_config.json`. Your real values therefore always win. For details, see
   [docs/credentials.md](docs/credentials.md).
3. **Send one notice.** Run `python main.py` once first, so there are
   duplicates to notify about.
   ```bash
   python main.py notify --send-live --limit 1
   ```
4. **Register the server with Claude Code.** The server reads `.env` itself,
   so the command needs no credentials.
   ```bash
   claude mcp add gmail --scope local -- "$PWD/.venv/bin/python" "$PWD/mcp_server/gmail_server.py"
   claude mcp list                              # gmail … ✔ Connected
   ```
   After restarting Claude Code, you can ask it to send notices with
   `send_email`. The pipeline doesn't need Claude to run: it has its own MCP
   client.

### Live-mode safety

- **Notices go to your own inbox.** They are sent to `GMAIL_ADDRESS` by
  default; `--test-recipient` picks another inbox. Notices are never sent to
  the real Enron senders. The intended recipient is kept in an
  `X-Original-To` header, and the body follows the assignment's template
  exactly.
- **Sending is capped.** `--limit` caps the send attempts per run (default 5),
  because Gmail allows about 500 emails a day.
- **Recorded sends are skipped.** Notices already marked as sent are skipped,
  so the next run picks up where the last one stopped. Delivery is not
  guaranteed exactly once (see [Known limitations](#known-limitations)).
- **Every attempt is logged.** Each one goes into `output/send_log.csv`. If a
  send fails, the failure is logged and the run carries on.

## Usage

| Command | What it does |
|---|---|
| `python main.py` | Runs every stage in order (same as `python main.py run`) |
| `python main.py ingest` | Parses the mailboxes and loads them into SQLite |
| `python main.py dedupe` | Flags duplicates and writes `duplicates_report.csv` |
| `python main.py notify` | Writes one `.eml` draft per flagged email |
| `… --send-live --limit N` | Also sends up to N notices through MCP |
| `… --reset` | Deletes `enron.db` and rebuilds it. See [Known limitations](#known-limitations). |

Other options:

| Option | Meaning |
|---|---|
| `--mailboxes lay-k skilling-j …` | Which mailboxes to process |
| `--threshold 90` | Similarity cutoff, from 0 to 100 |
| `--db path` | Where the database file lives |
| `--test-recipient` | The inbox that receives live notices |
| `--mcp-config` | The MCP configuration file to use |

## Architecture

```
┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│ 1 EXTRACT   │──►│ 2 STORE     │──►│ 3 DEDUPE    │──►│ 4 NOTIFY    │
│ find, parse │   │ SQLite      │   │ find, flag  │   │ drafts/send │
└─────────────┘   └─────────────┘   └─────────────┘   └──────┬──────┘
 error_log.txt       enron.db       duplicates_report.csv    │ --send-live
 extraction_stats                   dedupe_stats             ▼
                                         MCP client ──stdio──► gmail_server.py ──SMTP──► Gmail
```

**How data moves between stages:**
- **Extract → store:** parsed emails go straight to the database loader, in
  memory.
- **Dedupe and notify:** each reads its input from SQLite, so either one can
  run on its own once the earlier stages have filled the database.

Each module has **one job**:

| Layer | Module | Responsibility |
|---|---|---|
| Entry point | `main.py` | Wires the stages together; command-line options |
| Parsing | `parser.py` | Raw bytes → `ParsedEmail`. `parse_file()` reads one email file. |
| Logic, no I/O | `body.py` | Splits own text from forwarded and quoted text; finds headings and attachment references |
| | `dedupe.py` | Candidates → duplicate groups |
| | `stats.py` | Computes and formats statistics |
| Orchestration | `extract.py` | Parses many files and collects the failures |
| | `notify.py` | Builds notices and runs the send loop |
| I/O | `discovery.py` | Walks the maildir tree |
| | `db.py` | The **only** module that runs SQL |
| | `reports.py` | The **only** module that writes output files |
| | `mcp_client.py` | The pipeline's MCP client: starts the server and calls `send_email` |
| MCP server | `mcp_server/gmail_server.py` | Serves the `send_email` tool over MCP and sends through Gmail SMTP |
| Shared | `models.py`, `config.py`, `utils.py` | Data records, settings, generic helpers |

[`tests/test_architecture.py`](tests/test_architecture.py) checks these
boundaries automatically.

### Database ([schema.sql](schema.sql))

| Table | Holds |
|---|---|
| `emails` | One row per email: mandatory and optional fields, including `from_address`. Flag columns: `is_duplicate`, `duplicate_of` (refers to the original's `message_id`), `similarity_score`, `notification_sent`, `notification_date`. |
| `addresses` | Each recipient address, stored once |
| `email_recipients` | Links emails to addresses. `kind` is `to`, `cc` or `bcc`; `position` is the order within the header. |

- **Constraints:** `message_id` and `source_file` are UNIQUE. CHECK
  constraints guard the flag columns.
- **Indexes:** `date`, `from_address` and `subject`, plus `duplicate_of` and
  recipient lookups.
- **Dates** are UTC text in the form `YYYY-MM-DD HH:MM:SS`, which sorts
  chronologically.

## Mailbox selection

The assignment asks for at least 5 mailboxes and at least 10,000 emails. The
default subset has **5 mailboxes and 23,573 files**:

| Mailbox | Files | Who |
|---|---|---|
| `lay-k` | 5,938 | Kenneth Lay, Chairman and CEO |
| `skilling-j` | 4,140 | Jeff Skilling, CEO |
| `delainey-d` | 3,567 | David Delainey, CEO of Enron Energy Services |
| `lavorato-j` | 4,686 | John Lavorato, head of trading |
| `haedicke-m` | 5,247 | Mark Haedicke, General Counsel |

**Why these five:**
- **Different roles.** Executive, trading and legal mailboxes have very
  different styles: assistants writing for executives, newsletters, legal memos
  and automated system notices.
- **Hard cases for the parser.** Long forwarded chains, CC lists of up to 171
  recipients, and stripped attachments.
- **Fast to iterate on.** The full pipeline runs in about 20 seconds.

You can change the default in `config.py`, or per run with `--mailboxes`.

Of the **1,505 failed parses:**
- **1,504** have no valid `To` address. Most are undisclosed-recipient sends
  and calendar entries with no `To:` header at all.
- **1** has an invalid sender, `pep <performance.>`.

## Design decisions

### Parsing (Task 1)

- **Headers.** The parser uses Python's `email` package in its lenient mode.
  Bytes that aren't valid text are decoded as UTF-8, then Windows-1252, then
  Latin-1. In encoded headers, any line break or control character left after
  decoding becomes a space.
- **Recipients.** In Python 3.13 and later, one malformed entry makes
  `getaddresses` silently return an **empty list**. When that happens, a regex
  fallback recovers the valid addresses. Only real `user@domain` addresses are
  kept.
- **Dates.**
  - A complete RFC 2822 date goes to the standard library. Anything else goes
    to `dateutil` in strict mode, because the standard parser silently ignores
    `PM`.
  - US timezone abbreviations (PST, CDT, …) are converted to UTC offsets.
  - Unknown timezones and incomplete dates are logged as failures, never
    guessed.
  - Every date is stored in UTC.
- **Body.**
  - The body comes from the `text/plain` parts. If there are none, the
    `text/html` parts are converted to text.
  - Attachments are never used as the body, even when they have no filename.
  - `forwarded_content` starts at the first embedded-message marker, such as
    `-----Original Message-----` or `----- Forwarded by`.
  - `quoted_content` is the lines that start with `>`.
- **Display names.** `x_from`, `x_to`, `x_cc` and `x_bcc` hold display names
  only. For example, `"Lay, Kenneth" </O=ENRON/…>` becomes `Lay, Kenneth`.
- **Headings and attachments.**
  - A heading is a short, non-sentence line that is ALL CAPS, underlined, or a
    numbered title. Filters remove greetings, labels and initials.
  - `has_attachment` is true for MIME attachment parts, Outlook `<<file.xls>>`
    placeholders, and the `- file.doc` lines Enron's export left behind.

### Duplicates (Task 3)

The rule is the assignment's definition, applied literally. Two emails are
duplicates when they have:
- the same `from_address`,
- the same subject once `Re:`/`Fw:`/`Fwd:` prefixes are removed, and
- a `body` that is **≥ 90% similar**.

**Fuzzy library: [rapidfuzz](https://github.com/rapidfuzz/RapidFuzz)**
(`fuzz.ratio`). It's MIT-licensed and written in C++, scores similarity from 0
to 100 by edit distance, and stops early once a score can't reach the cutoff.
The whole scan takes about 2 seconds.

**Grouping.**
- Emails with the same sender and subject are compared oldest first. Each one
  joins the group whose **earliest email** it matches best, or starts a new
  group if it matches none.
- In every group, **all emails except the earliest are flagged**, and all of
  them appear in `duplicates_report.csv`. The **latest** duplicate is the one
  whose sender gets notified.
- When two emails have the same timestamp, the tie is broken by
  `source_file`, so results are the same every run.

### Notifications (Task 4)

- **Dry run:** an `.eml` draft for every flagged email, 10,627 in
  `output/replies/`.
- **Live mode:** one notice per group, addressed to the sender of the latest
  duplicate and sent through `send_email`. The redirect, cap and logging rules
  are under [Live-mode safety](#live-mode-safety).
- **Template.** Drafts and live notices both follow the assignment's template
  exactly: subject, `References` header, the flagged and original message
  blocks, the similarity score, and the CONFIRM text.
- **MCP server checks:**
  - Only `References`, `In-Reply-To`, `Reply-To` and `X-*` headers are
    accepted.
  - Line breaks in header values are rejected, which blocks header injection.
  - Recipients that Gmail refuses are reported.
  - Errors come back to the caller with a readable reason.

## Known limitations

### Specification choices

These follow from applying the assignment's wording literally:

- **Empty bodies always match.** The rule compares `body` only, and `body`
  excludes forwarded content. So two pure forwards with empty bodies, from the
  same sender with the same subject, score 100%, whatever they forward.
  - On this subset, **586** flagged emails have an empty body, as does their
    original.
  - In **167** of them, the forwarded text isn't identical to the original's
    (compared after whitespace normalization).
  - In **68**, the forwarded and quoted content is less than 90% similar,
    measured with the same rapidfuzz metric. These pairs would *not* be
    duplicates if forwarded content were compared.
- **Bcc repeats Cc.** Bcc is captured exactly as written, as the assignment
  requires. This export copies Cc into Bcc, so people who were cc'd also
  appear as Bcc recipients.
- **Re-running needs `--reset` after parser changes.** Ingest skips any
  `message_id` that's already stored. **`--reset` also clears
  `notification_sent`**, so reset before any live sends, or restore the sent
  rows from `output/send_log.csv` afterwards.

### Implementation limitations

- **A crash at the wrong moment could cause a resend.** After a successful
  send, the database is updated before the log row is written, so a logging
  failure can't trigger a resend. But a crash between Gmail accepting a notice
  and the database update would leave that notice pending.
- **Scale.** The pipeline holds the subset in memory and compares emails
  pairwise within each group. It was tested on 23.5k emails. The full 517k
  dataset would need batched loading.

## Outputs

| File | Contents |
|---|---|
| `error_log.txt` | One line per failed file: `source_file<TAB>reason` |
| `output/extraction_stats.json` | Counts, failure reasons, per-field completeness |
| `duplicates_report.csv` | Each flagged email next to its original, with the similarity score |
| `output/dedupe_stats.json` | Group and flagged counts; average and largest group size |
| `output/replies/*.eml` | Dry-run drafts, one per flagged email |
| `output/send_log.csv` | Every live send attempt. Columns: timestamp, recipient, subject, status, error, intended recipient, duplicate's `message_id`. |
| `enron.db` | The SQLite database. It's **gitignored**; `python main.py` builds it. |

## How to verify

```bash
pytest -q                                                # 178 passed
python main.py                                           # full pipeline, dry run
sqlite3 -header -column enron.db < sample_queries.sql    # results match the "Expected" comments
head -3 duplicates_report.csv                            # the 7 required columns
ls output/replies | wc -l                                # 10627
python main.py notify --send-live --limit 1              # one live send; check your inbox
cat output/send_log.csv
```

To rebuild the database from scratch, for example after changing the parser,
run `python main.py --reset`. **This deletes `enron.db`, including the record
of sent notices.** Do it before any live sends, or restore the sent rows from
`output/send_log.csv` afterwards.

You can also check individual requirements in the database:

| Requirement | Command | Expected result |
|---|---|---|
| `message_id` is unique | `sqlite3 enron.db "INSERT INTO emails (message_id,date,from_address,subject,body,source_file) SELECT message_id,date,from_address,subject,body,'x' FROM emails LIMIT 1"` | `UNIQUE constraint failed: emails.message_id` |
| The `from_address` index is used | `sqlite3 enron.db "EXPLAIN QUERY PLAN SELECT * FROM emails WHERE from_address='kenneth.lay@enron.com'"` | `USING INDEX idx_emails_from_address` |
| Recipients aren't comma-separated strings | `sqlite3 enron.db "SELECT COUNT(*) FROM addresses WHERE email LIKE '%,%'"` | `0` |

## Requirements checklist

| Requirement | Where it's met |
|---|---|
| §2: at least 5 mailboxes and 10k emails, with the reasons | `config.py` · [Mailbox selection](#mailbox-selection) |
| §3: mandatory and optional fields | `parser.py` · `body.py` · `models.py` |
| Task 1: traversal, edge cases, error log, statistics | `discovery.py` · `parser.py` · `error_log.txt` · `output/extraction_stats.json` |
| Task 2: schema, normalization, unique `message_id`, indexes, sample queries | `schema.sql` · `db.py` · `sample_queries.sql` (7 queries) |
| Task 3: groups, flags, report, statistics | `dedupe.py` · `duplicates_report.csv` · `output/dedupe_stats.json` |
| Task 4: Gmail MCP server, `send_email`, template, config example | `mcp_server/gmail_server.py` · `notify.py` · `mcp_config.json.example` |
| §4.3 and §6: AI usage and MCP integration | [AI_USAGE.md](AI_USAGE.md) |
| §5: Python 3.10+, one command, `--send-live`, fuzzy library documented | `python main.py [--send-live]` · [Duplicates](#duplicates-task-3) |
| §5: no crash on malformed input | Every file is parsed inside try/except; failures go to `error_log.txt` |
| §7: deliverables | `README.md` · `AI_USAGE.md` · `schema.sql` · `sample_queries.sql` · `mcp_config.json.example` · `output/replies/` · `output/send_log.csv` · `duplicates_report.csv` · `error_log.txt` · `requirements.txt` |
