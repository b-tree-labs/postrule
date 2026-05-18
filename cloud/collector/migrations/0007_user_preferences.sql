-- Migration 0007: user preferences + per-user insights enrollment.
--
-- Lands the data shape behind the new /dashboard/settings and
-- /dashboard/insights surfaces. Spec at:
--   docs/working/saas-launch-tech-spec-2026-05-02.md (settings, §230+)
--   docs/working/launch-proposal-2026-05-07.md       (§4.2 insights)
--
-- Two changes, both additive (no destructive backfill):
--
-- 1. users gets two preference columns. They live on `users` (not a
--    side-table) because every authenticated request reads them via
--    /v1/whoami; co-locating with the auth lookup keeps that path one
--    indexed read. `telemetry_enabled` defaults to 1 (ON) per Q4
--    decision 2026-05-11 — sign-in flow announces this default before
--    the user lands on /dashboard/settings, so the column matches what
--    they consented to.
--
-- 2. insights_enrollments is a new table — one row per (user, enrolled
--    period). Mirrors the local file at ~/.postrule/insights-enroll that
--    the CLI writes (src/postrule/insights/enrollment.py). The dashboard
--    toggle and the CLI command (`postrule insights enroll`) are the two
--    ways into this table; both write the same shape so the row is
--    portable across surfaces. `left_at` IS NULL means "currently
--    enrolled". A user can re-enroll after leaving — the old row stays
--    on disk for audit, a new row is inserted.

-- =========================================================================
-- USERS — add preference columns.
-- SQLite ALTER TABLE ADD COLUMN is the safe additive path; new rows
-- pick up the default, old rows backfill to the default on read.
-- =========================================================================

ALTER TABLE users ADD COLUMN display_name TEXT;

-- Default ON per Q4 decision 2026-05-11. The signed-in user agreed to
-- this default at the consent moment during sign-in; the dashboard
-- toggle is the second way to opt out (POSTRULE_NO_TELEMETRY=1 is the
-- first). 1 = ON, 0 = OFF.
ALTER TABLE users ADD COLUMN telemetry_enabled INTEGER NOT NULL DEFAULT 1;

-- =========================================================================
-- INSIGHTS_ENROLLMENTS — per-user cohort enrollment audit trail.
-- A user is "currently enrolled" iff there's a row with left_at IS NULL.
-- Re-enrollment after leaving inserts a fresh row; older rows are kept
-- for audit so we can answer "how long has this account been in?".
-- =========================================================================

CREATE TABLE IF NOT EXISTS insights_enrollments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    -- ISO 8601 enrollment timestamp. Mirrors EnrollmentState.enrolled_at
    -- in the Python SDK so the row is portable between CLI + dashboard.
    enrolled_at TEXT NOT NULL DEFAULT (datetime('now')),
    -- NULL while the enrollment is active; set on leave.
    left_at TEXT,
    -- Best-effort timestamp of the most recent server-side sync (e.g.
    -- last verdict event accepted from this user, or the last cohort
    -- aggregator run that included them). Updated opportunistically by
    -- the events / aggregator paths; safe to read as NULL.
    last_sync_at TEXT,
    -- SHA-256 of the disclosure text the user accepted, for audit (same
    -- shape as EnrollmentState.consent_text_sha256 in the SDK).
    consent_text_sha256 TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Hot path: dashboard / SDK look up "is this user currently enrolled?"
-- Partial index keeps the working set small (only active rows).
CREATE INDEX IF NOT EXISTS idx_insights_enrollments_active
    ON insights_enrollments (user_id) WHERE left_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_insights_enrollments_user
    ON insights_enrollments (user_id);
