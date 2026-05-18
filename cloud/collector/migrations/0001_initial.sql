-- Copyright (c) 2026 B-Tree Ventures, LLC
-- SPDX-License-Identifier: LicenseRef-BSL-1.1
--
-- Postrule Insights — collector schema, v1.
--
-- Single append-only `events` table. Privacy posture per
-- docs/working/telemetry-program-design-2026-04-28.md:
--   - shape-only (no source code, no labels, no prompt content)
--   - account_hash is HMAC of email + server-rotating salt
--   - site_fingerprint is blake2b over normalized AST shape
--   - payload_json contains the whitelisted shape-only fields per event_type
--
-- Schema is intentionally flat for v1. Phase B (hosted dashboard) will
-- add joining tables (accounts, projects, switches) backed by Postgres
-- via Alembic — separate concern from this Worker-side D1 schema.

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Server-stamped on insert (independent from the client-side
    -- timestamp inside the payload). Used for ingestion-rate analysis
    -- and as the natural sort key for warehouse reads.
    received_at TEXT NOT NULL DEFAULT (datetime('now')),

    -- Client-stamped, ISO-8601 UTC. Trustable as long as the client
    -- clock isn't badly skewed; not authoritative for ordering.
    event_timestamp TEXT NOT NULL,

    -- One of: analyze | init_attempt | bench_phase_advance.
    -- Enforced by CHECK constraint so the warehouse can't accumulate
    -- unknown event types from misconfigured clients.
    event_type TEXT NOT NULL CHECK (event_type IN (
        'analyze',
        'init_attempt',
        'bench_phase_advance'
    )),

    -- Schema version on the event payload. Bumps allow forward-compat
    -- payload extensions without a hard-cutover migration.
    schema_version INTEGER NOT NULL,

    -- HMAC of (email + server-rotating salt). Salt rotates weekly per
    -- the design doc, so the same user appears under different hashes
    -- across weeks — opt-in re-identification by-design.
    -- Nullable: events from non-logged-in CLI invocations have no
    -- account hash; they're aggregate-only contributors.
    account_hash TEXT,

    -- blake2b digest over the function's normalized AST. Identical
    -- across renames + literal changes, distinct across structural
    -- changes. Nullable for non-site-bound events (analyze run-level).
    site_fingerprint TEXT,

    -- The shape-only payload, as canonical JSON. The Worker validates
    -- against the per-event_type whitelist before insert; unknown keys
    -- are stripped server-side as defense-in-depth (the client also
    -- strips, but we don't trust the client to enforce privacy alone).
    payload_json TEXT NOT NULL,

    -- Cloudflare-injected request metadata: country, ASN, user-agent.
    -- Stored for spam / abuse triage; never used in cohort analysis;
    -- expired after 30 days via a separate cleanup job (NYI v1.0).
    request_country TEXT,
    request_asn INTEGER,
    request_user_agent TEXT
);

-- Indexes serve the aggregator job's most-common queries:
--   - events since X (warehouse-wide)
--   - events for a given site_fingerprint
--   - events for a given account_hash (for GDPR Article 15 export)

CREATE INDEX IF NOT EXISTS idx_events_received_at ON events (received_at);
CREATE INDEX IF NOT EXISTS idx_events_site_fingerprint ON events (site_fingerprint);
CREATE INDEX IF NOT EXISTS idx_events_account_hash ON events (account_hash);
CREATE INDEX IF NOT EXISTS idx_events_event_type ON events (event_type);
