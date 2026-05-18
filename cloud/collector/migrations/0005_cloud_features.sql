-- Migration 0005: cloud-feature backing tables.
--
-- Two tables for the v1.0 cloud features advertised on the dashboard
-- and exercised by `postrule.cloud.team_corpus` + `postrule.cloud.registry`.
--
-- Privacy posture:
--   - `team_corpora.payload_json` is whatever the operator uploads via
--     `share_corpus(...)`. We treat it as opaque JSON; cap is enforced
--     server-side at insert time (16 KB).
--   - `registry_contributions.payload_json` is anonymized client-side
--     by `postrule.cloud.registry.anonymize` before upload (strips a
--     conservative key list: author, email, host, repo_url, etc.) and
--     re-validated server-side via the same key list.
--
-- Why on this DB: shared with users + api_keys so an authed Worker
-- request can resolve api_key → user → row in a single round-trip.

CREATE TABLE IF NOT EXISTS team_corpora (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    -- Operator-chosen team identifier. Free-form string up to 128 chars;
    -- members coordinate the value out-of-band (e.g. "acme-eng").
    -- Multiple users can share the same team_id; isolation is by
    -- convention, not by enforced membership in v1.0. v1.1 ties this
    -- to a real team-membership model in the dashboard.
    team_id TEXT NOT NULL,
    -- Opaque corpus body. JSON-string. Server caps to 16 KB at insert.
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Hot path: GET /v1/team-corpus/:id returns the most recent corpus for
-- a given team_id. Index on (team_id, created_at DESC) makes that O(log N).
CREATE INDEX IF NOT EXISTS idx_team_corpora_team_id_time
    ON team_corpora (team_id, created_at DESC);

-- Per-user listing: a future "my contributions" dashboard view.
CREATE INDEX IF NOT EXISTS idx_team_corpora_user_id
    ON team_corpora (user_id);

CREATE TABLE IF NOT EXISTS registry_contributions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    -- Anonymized corpus body. JSON-string. Server validates that the
    -- conservative-identifier-key set has been stripped before insert,
    -- and caps to 32 KB.
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_registry_contributions_user_id
    ON registry_contributions (user_id);
