-- Migration 0003: SaaS launch schema — users, api_keys, subscriptions,
-- usage_metrics, daily_cost_projections, kill_switch.
--
-- Lands the data shape behind the v1.0 SaaS surface. Spec at
-- docs/working/saas-launch-tech-spec-2026-05-02.md.
--
-- Privacy + safety posture:
--   * email is the only PII; tokens/keys are argon2id-hashed at rest
--   * stripe_customer_id is null until first checkout
--   * rate_limit_rps_override is per-key (Business+ tier custom limits)
--   * kill_switch row is a single-row config table; tripped by the
--     cost-alarm cron Worker, cleared manually after investigation.

-- =========================================================================
-- USERS — one row per Clerk user. Account-hash matches the existing
-- insights privacy convention (HMAC of email). stripe_customer_id is
-- null until the user reaches the billing flow.
-- =========================================================================

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clerk_user_id TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL,
    account_hash TEXT NOT NULL,
    stripe_customer_id TEXT,
    -- 'free' | 'pro' | 'scale' | 'business'. Default 'free' until a
    -- subscription webhook upgrades the row.
    current_tier TEXT NOT NULL DEFAULT 'free',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_users_clerk_id ON users (clerk_user_id);
CREATE INDEX IF NOT EXISTS idx_users_stripe_customer
    ON users (stripe_customer_id);

-- =========================================================================
-- API_KEYS — issued via the dashboard. Plaintext returned ONCE at
-- issuance; only the argon2id hash persists. Multiple keys per user
-- (per-project use case). Rate limit is COALESCE(override, tier_default).
-- =========================================================================

CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    -- First 8 chars of the plaintext key (after the `dndr_live_` prefix)
    -- — displayable in the dashboard so users recognize their key. The
    -- last 4 chars are also stored for the same purpose.
    key_prefix TEXT NOT NULL,
    key_suffix TEXT NOT NULL,
    -- Argon2id hash of the full plaintext. Lookup uses an index on this.
    key_hash TEXT NOT NULL,
    -- User-given label (e.g. "production", "dev-laptop", "ci").
    name TEXT,
    -- Per-key rate-limit override. NULL means "use tier default".
    -- Only writeable for Business+ tier customers via Postrule ops.
    rate_limit_rps_override INTEGER,
    last_used_at TEXT,
    -- Soft-deleted; index excludes revoked keys so auth lookup is fast.
    revoked_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Lookup index — auth middleware queries by hash with revoked_at IS NULL.
-- Partial index keeps the working set small and the lookup fast.
CREATE INDEX IF NOT EXISTS idx_api_keys_hash_active
    ON api_keys (key_hash) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys (user_id);

-- =========================================================================
-- SUBSCRIPTIONS — mirrors Stripe subscription state via webhooks.
-- One active row per user (older rows kept for audit history).
-- =========================================================================

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    stripe_subscription_id TEXT NOT NULL UNIQUE,
    -- 'pro' | 'scale' | 'business' (free is implicit — no subscription).
    tier TEXT NOT NULL,
    -- 'active' | 'past_due' | 'canceled' | 'trialing'.
    status TEXT NOT NULL,
    current_period_start TEXT NOT NULL,
    current_period_end TEXT NOT NULL,
    -- Stripe event_id of the most recent webhook that mutated this row.
    -- Used for idempotency: replay-safe webhook handling.
    last_event_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON subscriptions (user_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_status
    ON subscriptions (status) WHERE status != 'canceled';

-- =========================================================================
-- USAGE_METRICS — per-key monthly classification count. Period_start
-- is "YYYY-MM" UTC. Atomic INSERT...ON CONFLICT DO UPDATE...RETURNING
-- on the verdict path keeps cap enforcement race-free.
-- =========================================================================

CREATE TABLE IF NOT EXISTS usage_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    api_key_id INTEGER NOT NULL REFERENCES api_keys(id),
    period_start TEXT NOT NULL,  -- "2026-05" format
    classifications_count INTEGER NOT NULL DEFAULT 0,
    -- Overage tracked separately so billing can pull it independently.
    overage_classifications INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One row per (key, month) — enforced by unique index for the upsert.
CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_key_period
    ON usage_metrics (api_key_id, period_start);

-- =========================================================================
-- DAILY_COST_PROJECTIONS — written nightly by the cost-alarm cron Worker.
-- One row per UTC day. Carries the projection that drives the
-- kill-switch trip decision.
-- =========================================================================

CREATE TABLE IF NOT EXISTS daily_cost_projections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- "2026-05-02" format. Unique constraint enforces one row per day.
    date TEXT NOT NULL UNIQUE,
    -- Yesterday's actual billable Cloudflare cost (USD).
    actual_yesterday_usd REAL NOT NULL,
    -- Linear projection of the current month's bill at this run rate.
    projected_month_end_usd REAL NOT NULL,
    -- Threshold in effect when this row was written (env-configurable;
    -- $300 default per Ben 2026-05-02). Stored so alarm logic remains
    -- auditable if the threshold changes.
    threshold_usd REAL NOT NULL,
    -- Did this row trigger the kill switch?
    triggered_alarm INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- =========================================================================
-- KILL_SWITCH — single-row config. Tripped by the cost-alarm Worker;
-- cleared manually via wrangler d1 execute after investigation.
-- =========================================================================

CREATE TABLE IF NOT EXISTS kill_switch (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- single-row constraint
    free_tier_throttle INTEGER NOT NULL DEFAULT 0,
    tripped_at TEXT,
    -- Free-form note for the next operator to read when investigating.
    reason TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Seed the single config row.
INSERT OR IGNORE INTO kill_switch (id, free_tier_throttle) VALUES (1, 0);
