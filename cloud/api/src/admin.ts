// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1
//
// Admin endpoints for the dashboard. Mounted at /admin/*. Auth is by
// shared service token (env.DASHBOARD_SERVICE_TOKEN) instead of Bearer
// API key — the dashboard's Clerk-authenticated route handlers are the
// only legitimate caller.
//
// Routes:
//   POST   /admin/users         — upsert a Clerk user; returns user_id
//   POST   /admin/keys          — issue a new API key for a user
//   GET    /admin/keys?user_id  — list a user's keys (metadata only)
//   DELETE /admin/keys/:id      — revoke (soft-delete) a key

import { Hono, type MiddlewareHandler } from 'hono';
import { generateKey, type KeyEnvironment } from './keys';
import { signLicense, type LicenseClaims } from './license';
import { TIER_MONTHLY_CAP, periodOf } from './usage';
import type { ApiEnv, AuthContext } from './auth';
import { preferences } from './preferences';
import { computeReport, phaseLabel } from './report';

export interface AdminEnv extends ApiEnv {
  DASHBOARD_SERVICE_TOKEN: string;
  LICENSE_SIGNING_PRIVATE_KEY?: string;
  KV_INSIGHTS: KVNamespace;
}

/**
 * Constant-time service-token comparison. The token is a fixed string
 * known to the dashboard; any timing leak would let an attacker probe
 * its bytes one at a time.
 */
function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}

const serviceTokenAuth = (): MiddlewareHandler<{ Bindings: AdminEnv }> =>
  async (c, next) => {
    const expected = c.env.DASHBOARD_SERVICE_TOKEN;
    if (!expected) {
      console.error('DASHBOARD_SERVICE_TOKEN not configured');
      return c.json({ error: 'server_misconfigured' }, 500);
    }
    const got = c.req.header('X-Dashboard-Token') ?? '';
    if (!timingSafeEqual(got, expected)) {
      return c.json({ error: 'unauthorized' }, 401);
    }
    await next();
  };

/**
 * Stable HMAC-style hash of an email for `account_hash`. The collector
 * uses HMAC-SHA-256(email, pepper) for events; we keep the same shape
 * so the two systems stay correlatable.
 */
async function accountHash(email: string, pepper: string): Promise<string> {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    enc.encode(pepper),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(email.toLowerCase()));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

export const admin = new Hono<{ Bindings: AdminEnv }>();
admin.use('*', serviceTokenAuth());

// ---------------------------------------------------------------------------
// POST /v1/admin/users — upsert a Clerk user. Idempotent.
// ---------------------------------------------------------------------------
admin.post('/users', async (c) => {
  const body = await c.req.json<{ clerk_user_id?: string; email?: string }>().catch(() => null);
  if (!body?.clerk_user_id || !body.email) {
    return c.json({ error: 'missing_clerk_user_id_or_email' }, 400);
  }

  const ah = await accountHash(body.email, c.env.API_KEY_PEPPER);

  await c.env.DB.prepare(
    `INSERT INTO users (clerk_user_id, email, account_hash)
     VALUES (?, ?, ?)
     ON CONFLICT(clerk_user_id) DO UPDATE SET
       email = excluded.email,
       account_hash = excluded.account_hash,
       updated_at = datetime('now')`,
  )
    .bind(body.clerk_user_id, body.email, ah)
    .run();

  const row = await c.env.DB.prepare(
    `SELECT id, current_tier, account_hash FROM users WHERE clerk_user_id = ?`,
  )
    .bind(body.clerk_user_id)
    .first<{ id: number; current_tier: string; account_hash: string }>();

  if (!row) return c.json({ error: 'upsert_failed' }, 500);
  return c.json({
    user_id: row.id,
    tier: row.current_tier,
    account_hash: row.account_hash,
  });
});

// ---------------------------------------------------------------------------
// POST /v1/admin/users/:user_id/set-tier — set a user's current_tier directly.
//
// Body: { tier: 'free' | 'pro' | 'scale' | 'business' | 'comp' }
//
// Used to provision no-charge `comp` (partner / internal-use) licenses
// without going through Stripe. The Stripe webhook path
// (subscription.created/updated/deleted) is the normal way tiers change;
// this endpoint is the operator escape hatch for tiers Stripe doesn't
// know about (`comp`) or for re-tiering an existing user (e.g. bumping
// a comp user who's legitimately exceeded the scale-equivalent cap).
//
// Operator responsibility: when setting tier='comp', ensure no active
// Stripe subscription exists for the user (otherwise the next webhook
// event will overwrite the tier). See docs/ops/comp-license-provisioning.md.
// ---------------------------------------------------------------------------
const VALID_TIERS = ['free', 'pro', 'scale', 'business', 'comp'] as const;
type ValidTier = (typeof VALID_TIERS)[number];

admin.post('/users/:user_id/set-tier', async (c) => {
  const userIdParam = c.req.param('user_id');
  const userId = Number(userIdParam);
  if (!Number.isInteger(userId) || userId <= 0) {
    return c.json({ error: 'invalid_user_id' }, 400);
  }

  const body = await c.req.json<{ tier?: string }>().catch(() => null);
  if (!body?.tier) {
    return c.json({ error: 'missing_tier' }, 400);
  }
  if (!(VALID_TIERS as readonly string[]).includes(body.tier)) {
    return c.json(
      { error: 'invalid_tier', valid_tiers: VALID_TIERS },
      400,
    );
  }
  const tier = body.tier as ValidTier;

  const updated = await c.env.DB.prepare(
    `UPDATE users
        SET current_tier = ?,
            updated_at = datetime('now')
      WHERE id = ?
     RETURNING id, email, current_tier, account_hash`,
  )
    .bind(tier, userId)
    .first<{ id: number; email: string; current_tier: string; account_hash: string }>();

  if (!updated) {
    return c.json({ error: 'user_not_found' }, 404);
  }

  return c.json({
    user_id: updated.id,
    email: updated.email,
    tier: updated.current_tier,
    account_hash: updated.account_hash,
  });
});

// ---------------------------------------------------------------------------
// POST /v1/admin/keys — issue a new API key. Returns plaintext ONCE.
// Body: { user_id: number, name?: string, environment?: 'live' | 'test' }
// ---------------------------------------------------------------------------
admin.post('/keys', async (c) => {
  const body = await c.req.json<{
    user_id?: number;
    name?: string;
    environment?: KeyEnvironment;
  }>().catch(() => null);

  if (!body?.user_id || !Number.isInteger(body.user_id)) {
    return c.json({ error: 'missing_user_id' }, 400);
  }

  // Sanity: confirm the user exists. Otherwise FK insert below would fail
  // with a less-descriptive error.
  const user = await c.env.DB.prepare(`SELECT id FROM users WHERE id = ?`)
    .bind(body.user_id)
    .first();
  if (!user) return c.json({ error: 'user_not_found' }, 404);

  const env = body.environment ?? 'live';
  const issued = await generateKey(c.env.API_KEY_PEPPER, env);

  const result = await c.env.DB.prepare(
    `INSERT INTO api_keys (user_id, key_prefix, key_suffix, key_hash, name)
     VALUES (?, ?, ?, ?, ?)
     RETURNING id, created_at`,
  )
    .bind(body.user_id, issued.prefix, issued.suffix, issued.hash, body.name ?? null)
    .first<{ id: number; created_at: string }>();

  if (!result) return c.json({ error: 'insert_failed' }, 500);

  return c.json({
    id: result.id,
    plaintext: issued.plaintext, // shown once; client must persist
    prefix: issued.prefix,
    suffix: issued.suffix,
    name: body.name ?? null,
    environment: env,
    created_at: result.created_at,
  });
});

// ---------------------------------------------------------------------------
// GET /v1/admin/keys?user_id=N — list a user's keys (metadata only).
// ---------------------------------------------------------------------------
admin.get('/keys', async (c) => {
  const uid = Number(c.req.query('user_id'));
  if (!Number.isInteger(uid) || uid <= 0) {
    return c.json({ error: 'missing_or_invalid_user_id' }, 400);
  }

  const rows = await c.env.DB.prepare(
    `SELECT id, key_prefix, key_suffix, name, last_used_at, revoked_at, created_at
       FROM api_keys
      WHERE user_id = ?
      ORDER BY created_at DESC`,
  )
    .bind(uid)
    .all();

  return c.json({ keys: rows.results ?? [] });
});

// ---------------------------------------------------------------------------
// GET /admin/usage?user_id=N — return the user's current-period verdict
// usage + tier cap, summed across all of their api_keys.
//
// Used by the dashboard root page to render the tier+usage strip and to
// decide whether to surface the A7 earned-upgrade banner.
//
// Shape:
//   { tier, verdicts_this_period, cap, period_start, period_end }
//
// period_start / period_end are ISO 8601 timestamps for the current UTC
// calendar month (cap reset point). cap is null for unlimited tiers
// (none today — every tier has a cap — but the JSON shape supports it).
// ---------------------------------------------------------------------------
admin.get('/usage', async (c) => {
  const uid = Number(c.req.query('user_id'));
  if (!Number.isInteger(uid) || uid <= 0) {
    return c.json({ error: 'missing_or_invalid_user_id' }, 400);
  }

  const user = await c.env.DB.prepare(
    `SELECT current_tier FROM users WHERE id = ? LIMIT 1`,
  )
    .bind(uid)
    .first<{ current_tier: AuthContext['tier'] }>();
  if (!user) return c.json({ error: 'user_not_found' }, 404);

  // Defensive: an unknown tier shouldn't 500 the dashboard.
  const tier: AuthContext['tier'] = (user.current_tier in TIER_MONTHLY_CAP
    ? user.current_tier
    : 'free') as AuthContext['tier'];

  const now = new Date();
  const period = periodOf(now);

  // Sum the current period's classifications across every key the user
  // owns. usage_metrics is per-(api_key_id, period); the join via
  // api_keys.user_id keeps the SQL trivially indexed.
  const row = await c.env.DB.prepare(
    `SELECT COALESCE(SUM(m.classifications_count), 0) AS verdicts
       FROM usage_metrics m
       JOIN api_keys k ON k.id = m.api_key_id
      WHERE k.user_id = ?
        AND m.period_start = ?`,
  )
    .bind(uid, period)
    .first<{ verdicts: number }>();

  // Period bounds as ISO timestamps (start = first of current UTC month,
  // end = first of next UTC month). The dashboard renders "days left".
  const periodStart = new Date(
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1, 0, 0, 0),
  );
  const periodEnd = new Date(
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1, 0, 0, 0),
  );

  return c.json({
    tier,
    verdicts_this_period: row?.verdicts ?? 0,
    cap: TIER_MONTHLY_CAP[tier],
    period_start: periodStart.toISOString(),
    period_end: periodEnd.toISOString(),
  });
});

// ---------------------------------------------------------------------------
// GET /admin/verdicts/recent?user_id=N&limit=K — most-recent K verdicts
// across every api_key the user owns.
//
// Used by the dashboard root page to render the recent-activity feed.
// limit is clamped to [1, 50] so a misbehaving caller can't drain D1.
//
// Shape:
//   { verdicts: [{ id, switch_name, phase, rule_correct, model_correct,
//                  ml_correct, created_at }, ...] }
// ---------------------------------------------------------------------------
admin.get('/verdicts/recent', async (c) => {
  const uid = Number(c.req.query('user_id'));
  if (!Number.isInteger(uid) || uid <= 0) {
    return c.json({ error: 'missing_or_invalid_user_id' }, 400);
  }

  const rawLimit = Number(c.req.query('limit') ?? '5');
  const limit = Number.isFinite(rawLimit) && rawLimit > 0
    ? Math.min(Math.floor(rawLimit), 50)
    : 5;

  const rows = await c.env.DB.prepare(
    `SELECT v.id,
            v.switch_name,
            v.phase,
            v.rule_correct,
            v.model_correct,
            v.ml_correct,
            v.created_at
       FROM verdicts v
       JOIN api_keys k ON k.id = v.api_key_id
      WHERE k.user_id = ?
      ORDER BY v.created_at DESC, v.id DESC
      LIMIT ?`,
  )
    .bind(uid, limit)
    .all();

  return c.json({ verdicts: rows.results ?? [] });
});

// ---------------------------------------------------------------------------
// POST /admin/licenses/issue — sign a license token for a user.
// Body: { user_id: number, ttl_days?: number, max_seats?: number | null }
// Returns: { token, claims }. The plaintext token must be stored by the
// dashboard / handed to the customer; we don't persist it (only the
// license_id claim, which lets us revoke at lookup time later).
// ---------------------------------------------------------------------------
admin.post('/licenses/issue', async (c) => {
  const priv = c.env.LICENSE_SIGNING_PRIVATE_KEY;
  if (!priv) {
    return c.json({ error: 'license_signing_not_configured' }, 500);
  }

  const body = await c.req.json<{
    user_id?: number;
    ttl_days?: number;
    max_seats?: number | null;
  }>().catch(() => null);

  if (!body?.user_id || !Number.isInteger(body.user_id)) {
    return c.json({ error: 'missing_user_id' }, 400);
  }
  const ttlDays = Number.isFinite(body.ttl_days) && (body.ttl_days ?? 0) > 0
    ? Math.min(Math.floor(body.ttl_days as number), 365 * 3)
    : 30;
  const maxSeats =
    body.max_seats === undefined || body.max_seats === null
      ? null
      : Number.isInteger(body.max_seats) && (body.max_seats as number) > 0
        ? (body.max_seats as number)
        : null;

  const user = await c.env.DB.prepare(
    `SELECT id, current_tier, account_hash FROM users WHERE id = ? LIMIT 1`,
  )
    .bind(body.user_id)
    .first<{ id: number; current_tier: LicenseClaims['tier']; account_hash: string }>();
  if (!user) return c.json({ error: 'user_not_found' }, 404);

  const { token, claims } = await signLicense({
    privateKeyHex: priv,
    user_id: user.id,
    tier: user.current_tier,
    account_hash: user.account_hash,
    ttlSeconds: ttlDays * 86400,
    max_seats: maxSeats,
  });

  return c.json({ token, claims });
});

// ---------------------------------------------------------------------------
// DELETE /v1/admin/keys/:id — revoke (soft-delete) a key.
// Body: { user_id: number } — defense-in-depth, prevents cross-user revoke
// ---------------------------------------------------------------------------
admin.delete('/keys/:id', async (c) => {
  const id = Number(c.req.param('id'));
  if (!Number.isInteger(id) || id <= 0) {
    return c.json({ error: 'invalid_id' }, 400);
  }
  const body = await c.req.json<{ user_id?: number }>().catch(() => null);
  if (!body?.user_id) {
    return c.json({ error: 'missing_user_id' }, 400);
  }

  const result = await c.env.DB.prepare(
    `UPDATE api_keys
        SET revoked_at = datetime('now')
      WHERE id = ?
        AND user_id = ?
        AND revoked_at IS NULL`,
  )
    .bind(id, body.user_id)
    .run();

  if (!result.meta.changes) {
    return c.json({ error: 'not_found_or_already_revoked' }, 404);
  }
  return c.json({ revoked: true, id });
});

// ---------------------------------------------------------------------------
// CLI device-flow admin endpoints. Companion to /v1/device/* in device.ts.
//
// The dashboard's /cli-auth page calls these via the Clerk-authenticated
// dashboard route handler, which forwards using the service token.
// ---------------------------------------------------------------------------

// GET /admin/cli-sessions/:user_code — pre-authorize lookup.
// Returns metadata the dashboard renders for the user to confirm
// before they click Authorize. NEVER returns device_code (CLI's secret).
admin.get('/cli-sessions/:user_code', async (c) => {
  const userCode = c.req.param('user_code');
  if (!userCode) return c.json({ error: 'missing_user_code' }, 400);

  const row = await c.env.DB.prepare(
    `SELECT state, device_name, created_at, expires_at, authorized_at
       FROM cli_sessions WHERE user_code = ? LIMIT 1`,
  )
    .bind(userCode)
    .first<{
      state: string;
      device_name: string | null;
      created_at: string;
      expires_at: string;
      authorized_at: string | null;
    }>();
  if (!row) return c.json({ error: 'not_found' }, 404);

  // Treat past-expires_at + 'pending' as expired for the dashboard's sake.
  const now = new Date().toISOString().replace('T', ' ').replace(/\..*$/, '');
  const effectiveState = row.state === 'pending' && row.expires_at < now ? 'expired' : row.state;

  return c.json({
    state: effectiveState,
    device_name: row.device_name,
    created_at: row.created_at,
    expires_at: row.expires_at,
    authorized_at: row.authorized_at,
  });
});

// POST /admin/cli-sessions/:user_code/authorize — dashboard authorizes.
// Body: { user_id: number }
admin.post('/cli-sessions/:user_code/authorize', async (c) => {
  const userCode = c.req.param('user_code');
  if (!userCode) return c.json({ error: 'missing_user_code' }, 400);

  const body = await c.req.json<{ user_id?: number }>().catch(() => null);
  if (!body?.user_id || !Number.isInteger(body.user_id)) {
    return c.json({ error: 'missing_user_id' }, 400);
  }

  // Atomic state transition: only pending + non-expired sessions can be
  // authorized. The WHERE clause prevents race-double-authorize.
  const updated = await c.env.DB.prepare(
    `UPDATE cli_sessions
        SET state = 'authorized',
            user_id = ?,
            authorized_at = datetime('now')
      WHERE user_code = ?
        AND state = 'pending'
        AND expires_at > datetime('now')
     RETURNING id, state`,
  )
    .bind(body.user_id, userCode)
    .first<{ id: number; state: string }>();

  if (!updated) {
    // Either not found, already authorized/denied/consumed, or expired.
    // Look up to disambiguate for the dashboard's error message.
    const existing = await c.env.DB.prepare(
      `SELECT state, expires_at FROM cli_sessions WHERE user_code = ? LIMIT 1`,
    )
      .bind(userCode)
      .first<{ state: string; expires_at: string }>();
    if (!existing) return c.json({ error: 'not_found' }, 404);

    const now = new Date().toISOString().replace('T', ' ').replace(/\..*$/, '');
    if (existing.state === 'pending' && existing.expires_at < now) {
      return c.json({ error: 'expired' }, 410);
    }
    return c.json({ error: `cannot_authorize_in_state_${existing.state}` }, 409);
  }

  return c.json({ ok: true });
});

// ---------------------------------------------------------------------------
// Settings + insights preferences. Same /admin/* prefix, same service-token
// auth (installed on the parent router above). Defined in preferences.ts to
// keep the handlers focused on settings concerns.
//   GET   /admin/whoami?user_id=N
//   PATCH /admin/whoami
//   GET   /admin/insights/status?user_id=N
//   POST  /admin/insights/enroll
//   POST  /admin/insights/leave
// ---------------------------------------------------------------------------
admin.route('/', preferences);

// POST /admin/cli-sessions/:user_code/deny — dashboard denies.
admin.post('/cli-sessions/:user_code/deny', async (c) => {
  const userCode = c.req.param('user_code');
  if (!userCode) return c.json({ error: 'missing_user_code' }, 400);

  const updated = await c.env.DB.prepare(
    `UPDATE cli_sessions
        SET state = 'denied'
      WHERE user_code = ?
        AND state = 'pending'
     RETURNING id`,
  )
    .bind(userCode)
    .first<{ id: number }>();

  if (!updated) return c.json({ error: 'not_found_or_already_decided' }, 404);
  return c.json({ ok: true });
});

// ---------------------------------------------------------------------------
// Switches roster + per-switch report — service-token proxy for the
// dashboard. The dashboard authenticates the user via Clerk, looks up
// the Postrule user_id via /admin/users (upsert), then calls these
// endpoints with the user_id in the query string.
//
// These mirror the bearer-authenticated /v1/switches surface but accept
// user_id via the service-token contract instead of resolving it from
// an api_key_id. Data isolation is enforced by the user_id WHERE clause
// in every query — the dashboard cannot read another user's data.
// ---------------------------------------------------------------------------

const SWITCH_NAME_RE = /^[A-Za-z][A-Za-z0-9_.-]{0,63}$/;
const SPARKLINE_DAYS = 14;

function utcDateStr(daysAgo: number, now: Date = new Date()): string {
  const d = new Date(now.getTime() - daysAgo * 86_400_000);
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, '0');
  const day = String(d.getUTCDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function lastNDates(n: number, now: Date = new Date()): string[] {
  const out: string[] = [];
  for (let i = n - 1; i >= 0; i--) out.push(utcDateStr(i, now));
  return out;
}

// GET /admin/switches?user_id=N[&include_archived=true] — list all
// switches owned by the user.
//
// Default (include_archived false / absent): only non-archived rows
// appear in `switches`. archived_count is still computed so the UI can
// decide whether to surface a "Show archived (N)" toggle.
//
// include_archived=true: archived rows are included; each row carries
// archived_at + archived_reason (null when not archived).
admin.get('/switches', async (c) => {
  const uid = Number(c.req.query('user_id'));
  if (!Number.isInteger(uid) || uid <= 0) {
    return c.json({ error: 'missing_or_invalid_user_id' }, 400);
  }
  const includeArchived = c.req.query('include_archived') === 'true';

  // LEFT JOIN switch_archives so archived state rides along with each
  // summary row. Filter is applied post-aggregation in the application
  // layer so archived_count stays accurate regardless of the toggle.
  //
  // The CTE pre-computes `phase_at_latest` per (user, switch) partition
  // via a single window-function pass; the outer GROUP BY projects it
  // back out. Replaces a correlated subquery that ran once per switch
  // in the result set — for a 500-switch user, that was 500 nested
  // SELECTs and pushed `/admin/switches` toward >2s on production D1
  // with edge RTT (see `docs/working/SCALE_REPORT-2026-05-11.md` §5.1).
  const summary = (
    await c.env.DB.prepare(
      `WITH user_verdicts AS (
         SELECT
           v.switch_name AS switch_name,
           v.phase       AS phase,
           v.created_at  AS created_at,
           FIRST_VALUE(v.phase) OVER (
             PARTITION BY v.switch_name
             ORDER BY v.created_at DESC
           ) AS phase_at_latest
         FROM verdicts v
         JOIN api_keys k ON k.id = v.api_key_id
         WHERE k.user_id = ?
       )
       SELECT
         uv.switch_name         AS switch_name,
         COUNT(*)               AS total_verdicts,
         MIN(uv.created_at)     AS first_activity,
         MAX(uv.created_at)     AS last_activity,
         sa.archived_at         AS archived_at,
         sa.archived_reason     AS archived_reason,
         MAX(uv.phase_at_latest) AS current_phase
       FROM user_verdicts uv
       LEFT JOIN switch_archives sa
              ON sa.user_id = ?
             AND sa.switch_name = uv.switch_name
       GROUP BY uv.switch_name, sa.archived_at, sa.archived_reason
       ORDER BY MAX(uv.created_at) DESC`,
    )
      .bind(uid, uid)
      .all<{
        switch_name: string;
        total_verdicts: number;
        first_activity: string;
        last_activity: string;
        archived_at: string | null;
        archived_reason: string | null;
        current_phase: string | null;
      }>()
  ).results ?? [];

  const archivedCount = summary.reduce(
    (acc, r) => acc + (r.archived_at ? 1 : 0),
    0,
  );

  // Sparkline window narrows to the non-archived set when we're hiding
  // archived rows — no point hitting D1 for buckets we won't render.
  const namesNeedingSpark = new Set(
    summary
      .filter((r) => includeArchived || !r.archived_at)
      .map((r) => r.switch_name),
  );

  const sparkRows = namesNeedingSpark.size === 0
    ? []
    : (
        await c.env.DB.prepare(
          `SELECT v.switch_name AS switch_name,
                  strftime('%Y-%m-%d', v.created_at) AS bucket_date,
                  COUNT(*) AS n
             FROM verdicts v
             JOIN api_keys k ON k.id = v.api_key_id
            WHERE k.user_id = ?
              AND v.created_at >= datetime('now', '-14 days')
            GROUP BY v.switch_name, strftime('%Y-%m-%d', v.created_at)`,
        )
          .bind(uid)
          .all<{ switch_name: string; bucket_date: string; n: number }>()
      ).results ?? [];

  const grid = lastNDates(SPARKLINE_DAYS);
  const byName = new Map<string, Map<string, number>>();
  for (const row of sparkRows) {
    let m = byName.get(row.switch_name);
    if (!m) {
      m = new Map();
      byName.set(row.switch_name, m);
    }
    m.set(row.bucket_date, row.n);
  }

  const filtered = includeArchived
    ? summary
    : summary.filter((r) => !r.archived_at);

  const switches = filtered.map((row) => ({
    switch_name: row.switch_name,
    current_phase: row.current_phase,
    current_phase_label: phaseLabel(row.current_phase),
    total_verdicts: row.total_verdicts,
    last_activity: row.last_activity,
    first_activity: row.first_activity,
    archived_at: row.archived_at,
    archived_reason: row.archived_reason,
    sparkline: grid.map((d) => byName.get(row.switch_name)?.get(d) ?? 0),
  }));

  return c.json({
    switches,
    sparkline_window_days: SPARKLINE_DAYS,
    archived_count: archivedCount,
  });
});

// GET /admin/switches/:name/report?user_id=N — per-switch report card.
// Returns 404 when the switch has no verdicts for the user (data
// isolation: never 200-empty so the dashboard can't render a confusing
// "empty card" for a stranger's switch name).
admin.get('/switches/:name/report', async (c) => {
  const uid = Number(c.req.query('user_id'));
  if (!Number.isInteger(uid) || uid <= 0) {
    return c.json({ error: 'missing_or_invalid_user_id' }, 400);
  }
  const switch_name = c.req.param('name') ?? '';
  if (!SWITCH_NAME_RE.test(switch_name)) {
    return c.json({ error: 'invalid_switch_name' }, 400);
  }
  const daysParam = Number(c.req.query('days') ?? '30');
  const days = Number.isFinite(daysParam) && daysParam > 0
    ? Math.min(Math.floor(daysParam), 365)
    : 30;

  const report = await computeReport(c.env.DB, uid, switch_name, days);
  if (!report) {
    return c.json({ error: 'switch_not_found' }, 404);
  }

  const currentPhase = report.transitions.length
    ? report.transitions[report.transitions.length - 1].phase
    : null;

  // Archive state rides along so the per-switch page can render the
  // archived banner instead of the stale banner. Single indexed read.
  const archive = await c.env.DB.prepare(
    `SELECT archived_at, archived_reason
       FROM switch_archives
      WHERE user_id = ? AND switch_name = ?
      LIMIT 1`,
  )
    .bind(uid, switch_name)
    .first<{ archived_at: string; archived_reason: string | null }>();

  return c.json({
    switch_name,
    days,
    agg: report.agg,
    phases: report.phases,
    transitions: report.transitions,
    current_phase: currentPhase,
    current_phase_label: phaseLabel(currentPhase),
    mcnemar_p_two_sided: report.mcnemar_p_two_sided,
    archived_at: archive?.archived_at ?? null,
    archived_reason: archive?.archived_reason ?? null,
  });
});

// ---------------------------------------------------------------------------
// Switch archive endpoints — customer-driven hide/show. Archiving never
// touches verdict history; auto-unarchive on next verdict (handled in
// cloud/api/src/verdicts.ts) is the natural recovery path.
//
// Both endpoints are idempotent: re-archiving returns the existing row,
// unarchiving an unarchived switch returns 200. Cross-account access is
// blocked by the ownership lookup — same 404 shape as the report card.
// ---------------------------------------------------------------------------

const MAX_ARCHIVED_REASON = 200;

// Local copy of the ownership check from switches.ts. We don't import to
// keep admin.ts self-contained against the existing switches.ts surface
// (which is bearer-auth scoped and uses AuthContext).
async function userOwnsSwitch(
  db: D1Database,
  user_id: number,
  switch_name: string,
): Promise<boolean> {
  if (!SWITCH_NAME_RE.test(switch_name)) return false;
  const row = await db
    .prepare(
      `SELECT 1 AS hit
         FROM verdicts v
         JOIN api_keys k ON k.id = v.api_key_id
        WHERE k.user_id = ?
          AND v.switch_name = ?
        LIMIT 1`,
    )
    .bind(user_id, switch_name)
    .first<{ hit: number }>();
  return !!row;
}

// POST /admin/switches/:name/archive — body: { user_id, reason? }.
// Idempotent: already-archived returns the existing row, not 409.
admin.post('/switches/:name/archive', async (c) => {
  const switch_name = c.req.param('name') ?? '';
  if (!SWITCH_NAME_RE.test(switch_name)) {
    return c.json({ error: 'invalid_switch_name' }, 400);
  }

  const body = await c.req.json<{ user_id?: number; reason?: string | null }>()
    .catch(() => null);
  if (!body?.user_id || !Number.isInteger(body.user_id) || body.user_id <= 0) {
    return c.json({ error: 'missing_or_invalid_user_id' }, 400);
  }

  let reason: string | null = null;
  if (body.reason !== undefined && body.reason !== null) {
    if (typeof body.reason !== 'string') {
      return c.json({ error: 'reason_must_be_string' }, 400);
    }
    if (body.reason.length > MAX_ARCHIVED_REASON) {
      return c.json(
        { error: `reason_exceeds_${MAX_ARCHIVED_REASON}_chars` },
        400,
      );
    }
    reason = body.reason.length === 0 ? null : body.reason;
  }

  // 404 (NOT 403) for cross-account / typo. Matches the report-card
  // contract — never leak existence.
  const owns = await userOwnsSwitch(c.env.DB, body.user_id, switch_name);
  if (!owns) {
    return c.json({ error: 'switch_not_found' }, 404);
  }

  // Idempotent upsert: ON CONFLICT keeps the original archived_at so the
  // audit timestamp doesn't slip on a repeat click. Reason is left
  // untouched on conflict for the same reason — the customer's first
  // explanation wins.
  await c.env.DB.prepare(
    `INSERT INTO switch_archives (user_id, switch_name, archived_reason)
     VALUES (?, ?, ?)
     ON CONFLICT(user_id, switch_name) DO NOTHING`,
  )
    .bind(body.user_id, switch_name, reason)
    .run();

  const row = await c.env.DB.prepare(
    `SELECT id, user_id, switch_name, archived_at, archived_reason
       FROM switch_archives
      WHERE user_id = ? AND switch_name = ?
      LIMIT 1`,
  )
    .bind(body.user_id, switch_name)
    .first<{
      id: number;
      user_id: number;
      switch_name: string;
      archived_at: string;
      archived_reason: string | null;
    }>();

  if (!row) {
    // Shouldn't happen — the INSERT (or pre-existing row) guarantees one.
    return c.json({ error: 'archive_failed' }, 500);
  }

  return c.json({ archive: row });
});

// POST /admin/switches/:name/unarchive — body: { user_id }.
// Idempotent: already-unarchived returns 200.
admin.post('/switches/:name/unarchive', async (c) => {
  const switch_name = c.req.param('name') ?? '';
  if (!SWITCH_NAME_RE.test(switch_name)) {
    return c.json({ error: 'invalid_switch_name' }, 400);
  }

  const body = await c.req.json<{ user_id?: number }>().catch(() => null);
  if (!body?.user_id || !Number.isInteger(body.user_id) || body.user_id <= 0) {
    return c.json({ error: 'missing_or_invalid_user_id' }, 400);
  }

  const owns = await userOwnsSwitch(c.env.DB, body.user_id, switch_name);
  if (!owns) {
    return c.json({ error: 'switch_not_found' }, 404);
  }

  await c.env.DB.prepare(
    `DELETE FROM switch_archives
      WHERE user_id = ? AND switch_name = ?`,
  )
    .bind(body.user_id, switch_name)
    .run();

  return c.json({ unarchived: true });
});
