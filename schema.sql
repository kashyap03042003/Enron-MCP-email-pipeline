-- Enron email database schema (SQLite).
--
-- Every statement is idempotent (IF NOT EXISTS), so applying the schema to
-- an existing database is safe; the pipeline does this on each run.
--
-- Conventions:
--   * Timestamps are TEXT in UTC, 'YYYY-MM-DD HH:MM:SS' (sortable, and
--     works with SQLite's date/time functions).
--   * Booleans are INTEGER 0/1.
--   * Absent optional values are NULL.

PRAGMA foreign_keys = ON;

-- Every distinct recipient address (To/Cc/Bcc), stored once and referenced
-- from email_recipients instead of as comma-separated strings.
CREATE TABLE IF NOT EXISTS addresses (
    id    INTEGER PRIMARY KEY,
    email TEXT    NOT NULL UNIQUE           -- lowercased
);

CREATE TABLE IF NOT EXISTS emails (
    id                INTEGER PRIMARY KEY,

    -- Mandatory fields
    message_id        TEXT    NOT NULL UNIQUE,      -- blocks exact duplicates on insert
    date              TEXT    NOT NULL,             -- UTC
    from_address      TEXT    NOT NULL,             -- sender address only, lowercased
    subject           TEXT    NOT NULL,             -- may be '' (header present but empty)
    body              TEXT    NOT NULL,             -- author's own text only
    source_file       TEXT    NOT NULL UNIQUE,      -- e.g. maildir/lay-k/inbox/1.

    -- Optional fields
    x_from            TEXT,
    x_to              TEXT,
    x_cc              TEXT,
    x_bcc             TEXT,
    x_folder          TEXT,
    x_origin          TEXT,
    content_type      TEXT,
    has_attachment    INTEGER NOT NULL DEFAULT 0 CHECK (has_attachment IN (0, 1)),
    forwarded_content TEXT,
    quoted_content    TEXT,
    headings          TEXT,                         -- one heading per line

    -- Duplicate detection (Task 3)
    is_duplicate      INTEGER NOT NULL DEFAULT 0 CHECK (is_duplicate IN (0, 1)),
    duplicate_of      TEXT    REFERENCES emails(message_id),   -- the original's message_id
    similarity_score  REAL    CHECK (similarity_score BETWEEN 0 AND 100),

    -- Notification tracking (Task 4)
    notification_sent INTEGER NOT NULL DEFAULT 0 CHECK (notification_sent IN (0, 1)),
    notification_date TEXT,                         -- UTC

    CHECK (is_duplicate = 0 OR duplicate_of IS NOT NULL)
);

-- To/Cc/Bcc recipients: one row per (email, kind, address).
CREATE TABLE IF NOT EXISTS email_recipients (
    email_id   INTEGER NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
    address_id INTEGER NOT NULL REFERENCES addresses(id),
    kind       TEXT    NOT NULL CHECK (kind IN ('to', 'cc', 'bcc')),
    position   INTEGER NOT NULL,                    -- order within the header
    PRIMARY KEY (email_id, kind, address_id)
);

CREATE INDEX IF NOT EXISTS idx_emails_date         ON emails(date);
CREATE INDEX IF NOT EXISTS idx_emails_from_address ON emails(from_address);
CREATE INDEX IF NOT EXISTS idx_emails_subject      ON emails(subject);
CREATE INDEX IF NOT EXISTS idx_emails_duplicate_of ON emails(duplicate_of);
CREATE INDEX IF NOT EXISTS idx_recipients_address  ON email_recipients(address_id);

