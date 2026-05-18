-- Migration 0002: leads table for the landing-page paste-analyzer
-- "email me my results" / "send to teammate" capture flow.
--
-- Privacy posture (per docs/working/landing-friction-reduction-2026-05-01.md):
-- * email is the only identifying field captured. NO source code is ever
--   sent — Pyodide runs locally and only the result counts cross the wire.
-- * teammate_email is captured ONLY when the visitor explicitly fills the
--   "send to teammate" field; never auto-populated.
-- * Cloudflare-edge metadata (country, ASN, user-agent) is preserved for
--   abuse triage on the same 30-day TTL the events table uses.
-- * No tracking cookies, no IP storage, no referrer.

CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL DEFAULT (datetime('now')),

    -- The visitor's email — required.
    email TEXT NOT NULL,

    -- Optional teammate forward target. Sent a one-line context note
    -- ("@<email> saw this on Postrule and thought you'd want a look")
    -- alongside the analysis when this is non-null.
    teammate_email TEXT,

    -- Result-shape signals from the in-browser analysis. Used to send
    -- the visitor a tailored Markdown summary; NOT used to reconstruct
    -- their pasted source.
    site_count INTEGER,
    top_priority_score REAL,
    top_pattern TEXT,        -- "P1".."P6"
    high_priority_count INTEGER,  -- sites with priority_score >= 4.0

    -- Cloudflare-edge abuse-triage metadata; cleanup job purges >30 d.
    request_country TEXT,
    request_asn INTEGER,
    request_user_agent TEXT
);

CREATE INDEX IF NOT EXISTS idx_leads_captured_at ON leads (captured_at);
CREATE INDEX IF NOT EXISTS idx_leads_email ON leads (email);
