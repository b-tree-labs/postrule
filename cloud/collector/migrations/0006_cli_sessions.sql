-- Migration 0006: cli_sessions table for the `postrule login` device flow.
--
-- Backs RFC 8628 (OAuth 2.0 Device Authorization Grant) for the CLI:
--
--   1. CLI calls POST /v1/device/code → row inserted with state='pending'
--      and a (device_code, user_code) pair. CLI displays user_code to
--      the user; user_code is short, human-typeable (XXXX-XXXX).
--   2. User opens https://app.postrule.ai/cli-auth, types user_code, clicks
--      Authorize. Dashboard calls /admin/cli-sessions/:user_code/authorize
--      → state transitions to 'authorized', user_id linked.
--   3. CLI polls POST /v1/device/token with device_code → server mints a
--      fresh dndr_live_… API key, transitions state to 'consumed', returns
--      the plaintext to the CLI exactly once. Plaintext is never stored.
--
-- Idempotency / race-safety:
--   - device_code and user_code are both UNIQUE — duplicate inserts fail at
--     the DB layer, so there's no need for application-level dedupe.
--   - The poll handler reads + transitions state in a single statement
--     (UPDATE … WHERE state='authorized' RETURNING) so two concurrent
--     polls from a misconfigured CLI can't double-mint a key.
--
-- Expiration:
--   - expires_at is set to (datetime('now', '+15 minutes')) at insert.
--   - The handler treats state='pending' AND expires_at < now() as expired.
--   - We don't actively transition expired rows — they stay 'pending' on
--     disk to keep the schema additive. A future cleanup job can sweep.
--
-- Privacy:
--   - device_name is operator-supplied (e.g. "ben-laptop"), shown to the
--     user in the dashboard so they can confirm the device requesting
--     access. Treat as untrusted client-supplied text; capped at 64 chars
--     server-side at insert time.

CREATE TABLE IF NOT EXISTS cli_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Long random secret known only to the requesting CLI process.
    -- 43 url-safe base64 chars = 256 bits of entropy. Used as the bearer
    -- token in /v1/device/token.
    device_code TEXT NOT NULL UNIQUE,
    -- Short human-typeable code shown to the user (format XXXX-XXXX,
    -- 9 chars including the hyphen). Hyphen-separated for readability;
    -- the dashboard input strips the hyphen on submit.
    user_code TEXT NOT NULL UNIQUE,
    -- pending | authorized | consumed | denied
    -- (Expired sessions are 'pending' rows where expires_at < now;
    -- no separate 'expired' state in storage.)
    state TEXT NOT NULL DEFAULT 'pending',
    -- Set when state transitions to 'authorized'. FK to users.
    user_id INTEGER REFERENCES users(id),
    -- Set when state transitions to 'consumed'. FK to api_keys.
    api_key_id INTEGER REFERENCES api_keys(id),
    -- Optional client-supplied label for dashboard display.
    -- e.g. "ben-laptop", "ci-runner-3". Server caps at 64 chars on insert.
    device_name TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    authorized_at TEXT,
    consumed_at TEXT
);

-- Hot path: CLI polls with device_code as the lookup key.
CREATE INDEX IF NOT EXISTS idx_cli_sessions_device_code
    ON cli_sessions (device_code);

-- Hot path: dashboard looks up by user_code when the user types it.
CREATE INDEX IF NOT EXISTS idx_cli_sessions_user_code
    ON cli_sessions (user_code);

-- For per-user listing (a future "active CLI sessions" dashboard view).
CREATE INDEX IF NOT EXISTS idx_cli_sessions_user_id
    ON cli_sessions (user_id);
