-- Copyright (c) 2026 B-Tree Labs
-- SPDX-License-Identifier: LicenseRef-BSL-1.1
--
-- Migration 0010: security-disclosure report ledger.
--
-- Backs the cloud/security-ops/ Worker — the inbound side of the
-- published 72-hour-ack / 5-business-day-triage SLAs from SECURITY.md.
-- Every email received at security@b-treeventures.com lands here with
-- an auto-allocated reference; the cron handler escalates rows that
-- have been sitting in the inbox too long.
--
-- Cohabits with the existing dendra-events D1 (staging + production --
-- still named `dendra-events` in CF; rename deferred post-launch)
-- because there is no reason to provision a second database for a few
-- hundred rows over the next decade. Migrations live with the
-- collector for the same reason — single migrations_dir per database.
--
-- Schema is intentionally minimal: enough to allocate a reference,
-- compute "open / triaged / resolved" lifecycle, and answer "what
-- is overdue right now" from the cron handler. Operator state
-- transitions (triaged_at / resolved_at) are written by hand via
-- `wrangler d1 execute` until the operator UI lands post-launch.

CREATE TABLE IF NOT EXISTS security_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Human-quotable reference of the form SR-YYYY-NNNN where NNNN is a
    -- zero-padded counter that restarts every January 1. Allocated by
    -- counting the current-year rows + 1 inside a single transaction at
    -- insert time. UNIQUE guards against the (vanishingly unlikely but
    -- possible) race where two inbound emails land in the same millis.
    reference TEXT NOT NULL UNIQUE,

    -- ISO-8601 UTC. Server-stamped at insert from the email handler so
    -- it can't drift from the From-header date the sender chose.
    received_at TEXT NOT NULL,

    -- The inbound email's From: address. Stored verbatim for replyability
    -- since Cloudflare Email Routing hands us the original envelope.
    sender TEXT NOT NULL,

    -- Subject line. Nullable because some clients send mail with no
    -- Subject; we still want to log it.
    subject TEXT,

    -- 1 if the subject contains URGENT (case-insensitive). Drives the
    -- separate >24h immediate-escalation path in the cron handler.
    urgent INTEGER NOT NULL DEFAULT 0,

    -- ISO-8601 UTC stamped after the auto-reply is dispatched. NULL if
    -- the auto-reply failed; the row still exists so the operator can
    -- ack manually and then update this column.
    acked_at TEXT,

    -- ISO-8601 UTC stamped by the operator (via `wrangler d1 execute`
    -- until the dashboard UI lands) once the report has been triaged.
    -- The cron handler considers a row "open" while this is NULL.
    triaged_at TEXT,

    -- ISO-8601 UTC stamped when the disclosure is closed out — fix
    -- shipped or report deemed not-applicable. Distinct from triaged
    -- so we can measure ack→triage and triage→resolve separately.
    resolved_at TEXT,

    -- Free-form operator notes. Anything the operator wants future-Ben
    -- to know — sender's PGP fingerprint, severity assessment, etc.
    notes TEXT
);

-- The cron handler's hot path: "find every row with triaged_at IS NULL
-- ordered by received_at" so we can age-bucket them. Putting triaged_at
-- first means the index head is dense (most rows are NULL until they
-- aren't), and received_at second makes the ORDER BY index-only.
CREATE INDEX IF NOT EXISTS idx_security_reports_triaged
    ON security_reports (triaged_at, received_at);
