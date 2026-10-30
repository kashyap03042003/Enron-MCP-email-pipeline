-- Sample queries against enron.db (built by `python main.py ingest`).
--
-- Run:  sqlite3 -header -column enron.db < sample_queries.sql
--
-- Expected output below was recorded on the default five-mailbox subset
-- (lay-k, skilling-j, delainey-d, lavorato-j, haedicke-m; 22,068 emails).


-- 1. Emails sent per sender (top 5).
--    Uses the email_details view, which joins in the sender's address.
--    Expected: the mailbox owners lead, followed by their assistants:
--      david.delainey@enron.com   2948
--      john.lavorato@enron.com    2280
--      mark.haedicke@enron.com    1828
--      rosalee.fleming@enron.com   885   (Ken Lay's assistant)
--      sherri.sera@enron.com       674   (Jeff Skilling's assistant)
SELECT from_address, COUNT(*) AS emails_sent
FROM email_details
GROUP BY from_address
ORDER BY emails_sent DESC
LIMIT 5;


-- 2. Emails in a date range: Q4 2001, the months around Enron's collapse.
--    Dates are stored in UTC as 'YYYY-MM-DD HH:MM:SS', so string comparison
--    is chronological and can use idx_emails_date.
--    Expected: 2400 emails, first 2001-10-01 05:27:19, last 2001-12-31 21:47:13.
SELECT COUNT(*) AS emails_in_range,
       MIN(date)  AS first_email,
       MAX(date)  AS last_email
FROM emails
WHERE date >= '2001-10-01' AND date < '2002-01-01';


-- 3. Emails with CC recipients, the widest CC lists first.
--    Expected: 5645 emails have at least one CC. Top row is "Program Date
--    Change" (2001-10-05) with 171 CC recipients. Several subjects appear
--    twice with different message_ids: these are copies of one email
--    filed in two folders, which duplicate detection (Task 3) flags.
SELECT e.message_id, e.date, e.subject, COUNT(*) AS cc_count
FROM emails e
JOIN email_recipients r ON r.email_id = e.id AND r.kind = 'cc'
GROUP BY e.id
ORDER BY cc_count DESC
LIMIT 5;


-- 4. Most frequent recipients across To/Cc/Bcc (top 5).
--    Expected: kenneth.lay@enron.com 2879, john.lavorato@enron.com 2861,
--    jeff.skilling@enron.com 2591, klay@enron.com 2012 (Lay's alias),
--    mark.haedicke@enron.com 1920. (Bcc is stored as written, and in this
--    export it repeats Cc, so Cc'd people are counted under both roles.)
SELECT a.email, COUNT(*) AS times_received
FROM email_recipients r
JOIN addresses a ON a.id = r.address_id
GROUP BY a.id
ORDER BY times_received DESC
LIMIT 5;


-- 5. Everything one person sent OR received. The shared addresses table
--    makes this one lookup instead of matching strings in two places.
--    Expected: one row per role, so an email where Skilling is both a To and
--    a Bcc recipient appears twice ('to' and 'bcc'); role is 'from', 'to',
--    'cc' or 'bcc'.
SELECT e.date, e.subject, 'from' AS role
FROM emails e
JOIN addresses a ON a.id = e.sender_id
WHERE a.email = 'jeff.skilling@enron.com'
UNION ALL
SELECT e.date, e.subject, r.kind
FROM emails e
JOIN email_recipients r ON r.email_id = e.id
JOIN addresses a ON a.id = r.address_id
WHERE a.email = 'jeff.skilling@enron.com'
ORDER BY date DESC
LIMIT 10;


-- 6. Attachment rate per mailbox (mailbox taken from source_file).
--    Expected:
--      lay-k       5601 emails  800 with attachment  14.3%
--      haedicke-m  4776         392                   8.2%
--      lavorato-j  4417         134                   3.0%
--      skilling-j  3744         510                  13.6%
--      delainey-d  3530          78                   2.2%
SELECT substr(source_file, 9, instr(substr(source_file, 9), '/') - 1) AS mailbox,
       COUNT(*)                                          AS emails,
       SUM(has_attachment)                               AS with_attachment,
       ROUND(100.0 * SUM(has_attachment) / COUNT(*), 1)  AS pct
FROM emails
GROUP BY mailbox
ORDER BY emails DESC;


-- 7. Largest duplicate groups (after `python main.py dedupe`).
--    Each flagged email points at its original through duplicate_of.
--    Expected: 10627 emails flagged out of 22068. The largest groups are
--    repeated automated or template emails:
--      Approval for reviewer   2000-11-06  67 duplicates, latest 2000-11-21
--      New Kudlow Commentary   2001-10-12  28 duplicates, latest 2002-01-18
--      (empty subject)         2000-06-05  22 duplicates, latest 2001-01-23
SELECT o.message_id     AS original,
       o.subject,
       o.date           AS original_date,
       COUNT(d.id)      AS duplicates,
       MAX(d.date)      AS latest_duplicate
FROM emails o
JOIN emails d ON d.duplicate_of = o.message_id
GROUP BY o.id
ORDER BY duplicates DESC, o.date
LIMIT 3;
