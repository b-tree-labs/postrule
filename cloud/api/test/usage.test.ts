// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1
//
// Monthly-cap enforcement tests. Exercises the upsert path, the
// hard-cap path (Free tier), the soft-cap/overage path (Pro), and
// the period-boundary date math.

import { describe, it, expect, beforeAll } from 'vitest';
import { env, SELF } from 'cloudflare:test';
import migration0001 from '../../collector/migrations/0001_initial.sql?raw';
import migration0002 from '../../collector/migrations/0002_leads.sql?raw';
import migration0003 from '../../collector/migrations/0003_saas.sql?raw';
import migration0004 from '../../collector/migrations/0004_verdicts.sql?raw';
// Migration 0007 adds users.display_name + users.telemetry_enabled, which
// /v1/whoami now selects. Apply here so the whoami-doesn't-count-toward-usage
// test doesn't fail on a missing column when this suite runs before
// preferences.test.ts.
import migration0007 from '../../collector/migrations/0007_user_preferences.sql?raw';
// Migration 0008 (switch_archives) is referenced by the verdict-emit hot
// path for auto-unarchive — without it the INSERT INTO verdicts succeeds
// but the follow-up DELETE FROM switch_archives 500s. Apply here so the
// billable POST /v1/verdicts assertion doesn't depend on suite order.
import migration0008 from '../../collector/migrations/0008_switch_archives.sql?raw';
import { recordUsage, periodOf, secondsUntilNextPeriod } from '../src/usage';

const SERVICE_TOKEN = 'test-service-token-for-dashboard';
const BASE = 'https://api.test';

const adminHeaders = {
  'Content-Type': 'application/json',
  'X-Dashboard-Token': SERVICE_TOKEN,
};

async function applySql(sql: string) {
  const cleaned = sql
    .split('\n')
    .filter((l) => !l.trim().startsWith('--'))
    .join('\n');
  const stmts = cleaned
    .split(/;\s*(?:\n|$)/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
  for (const s of stmts) {
    try {
      await env.DB.prepare(s).run();
    } catch (e) {
      const msg = String(e);
      // ALTER TABLE ADD COLUMN throws "duplicate column name" on re-apply.
      if (!msg.includes('already exists') && !msg.includes('duplicate column')) {
        throw e;
      }
    }
  }
}

beforeAll(async () => {
  await applySql(migration0001);
  await applySql(migration0002);
  await applySql(migration0003);
  await applySql(migration0004);
  await applySql(migration0007);
  await applySql(migration0008);
});

describe('periodOf / secondsUntilNextPeriod', () => {
  it('formats period as YYYY-MM in UTC', () => {
    expect(periodOf(new Date('2026-05-15T03:00:00Z'))).toBe('2026-05');
    expect(periodOf(new Date('2026-12-31T23:59:59Z'))).toBe('2026-12');
    expect(periodOf(new Date('2027-01-01T00:00:00Z'))).toBe('2027-01');
  });

  it('seconds-until-next-period rolls over correctly', () => {
    // 2026-05-15 12:00 UTC → next period at 2026-06-01 00:00 UTC
    const d = new Date('2026-05-15T12:00:00Z');
    const got = secondsUntilNextPeriod(d);
    // 16 days, 12 hours = 16.5 * 86400 = 1,425,600
    expect(got).toBe(16 * 86400 + 12 * 3600);
  });

  it('seconds-until-next-period from last day rolls into next month', () => {
    const d = new Date('2026-05-31T23:00:00Z');
    expect(secondsUntilNextPeriod(d)).toBe(3600);
  });
});

describe('recordUsage atomic increment', () => {
  let userId: number;
  let apiKeyId: number;

  beforeAll(async () => {
    // Create a Free-tier user + key for the upcoming tests.
    const u = await SELF.fetch(`${BASE}/admin/users`, {
      method: 'POST',
      headers: adminHeaders,
      body: JSON.stringify({
        clerk_user_id: 'usage_user_1',
        email: 'usage1@example.com',
      }),
    });
    userId = (await u.json<{ user_id: number }>()).user_id;

    const k = await SELF.fetch(`${BASE}/admin/keys`, {
      method: 'POST',
      headers: adminHeaders,
      body: JSON.stringify({ user_id: userId }),
    });
    apiKeyId = (await k.json<{ id: number }>()).id;
  });

  it('first call inserts, subsequent calls increment atomically', async () => {
    const auth = {
      user_id: userId,
      api_key_id: apiKeyId,
      tier: 'free' as const,
      account_hash: 'h',
      rate_limit_rps: 10,
    };
    const a = await recordUsage(env.DB, auth, 1);
    expect(a.classifications_count).toBe(1);
    expect(a.over_cap).toBe(false);
    const b = await recordUsage(env.DB, auth, 5);
    expect(b.classifications_count).toBe(6);
    expect(b.over_cap).toBe(false);
  });

  it('Free tier triggers hard cap when over 10K', async () => {
    const auth = {
      user_id: userId,
      api_key_id: apiKeyId,
      tier: 'free' as const,
      account_hash: 'h',
      rate_limit_rps: 10,
    };
    const r = await recordUsage(env.DB, auth, 10_000);
    expect(r.classifications_count).toBeGreaterThan(10_000);
    expect(r.over_cap).toBe(true);
    expect(r.over_cap_kind).toBe('hard');
  });

  it('Pro tier records overage (soft cap) past 250K', async () => {
    // Promote a separate user to Pro for clean accounting.
    const u = await SELF.fetch(`${BASE}/admin/users`, {
      method: 'POST',
      headers: adminHeaders,
      body: JSON.stringify({
        clerk_user_id: 'usage_user_pro',
        email: 'pro@example.com',
      }),
    });
    const proUserId = (await u.json<{ user_id: number }>()).user_id;
    await env.DB.prepare(`UPDATE users SET current_tier = 'pro' WHERE id = ?`)
      .bind(proUserId)
      .run();

    const k = await SELF.fetch(`${BASE}/admin/keys`, {
      method: 'POST',
      headers: adminHeaders,
      body: JSON.stringify({ user_id: proUserId }),
    });
    const proKeyId = (await k.json<{ id: number }>()).id;

    const auth = {
      user_id: proUserId,
      api_key_id: proKeyId,
      tier: 'pro' as const,
      account_hash: 'h',
      rate_limit_rps: 50,
    };

    // Bulk-load up to the cap edge in one go.
    const atCap = await recordUsage(env.DB, auth, 250_000);
    expect(atCap.over_cap).toBe(false);

    // One more units pushes over → soft cap + overage recorded.
    const over = await recordUsage(env.DB, auth, 100);
    expect(over.over_cap).toBe(true);
    expect(over.over_cap_kind).toBe('soft');
    expect(over.overage_classifications).toBe(100);
  });

  it('Comp tier hard-caps at 5M like scale (no overage since no billing)', async () => {
    const u = await SELF.fetch(`${BASE}/admin/users`, {
      method: 'POST',
      headers: adminHeaders,
      body: JSON.stringify({
        clerk_user_id: 'usage_user_comp',
        email: 'comp@example.com',
      }),
    });
    const compUserId = (await u.json<{ user_id: number }>()).user_id;
    await env.DB.prepare(`UPDATE users SET current_tier = 'comp' WHERE id = ?`)
      .bind(compUserId)
      .run();

    const k = await SELF.fetch(`${BASE}/admin/keys`, {
      method: 'POST',
      headers: adminHeaders,
      body: JSON.stringify({ user_id: compUserId }),
    });
    const compKeyId = (await k.json<{ id: number }>()).id;

    const auth = {
      user_id: compUserId,
      api_key_id: compKeyId,
      tier: 'comp' as const,
      account_hash: 'h',
      rate_limit_rps: 200,
    };

    // Bulk-load up to the cap edge.
    const atCap = await recordUsage(env.DB, auth, 5_000_000);
    expect(atCap.over_cap).toBe(false);

    // One more pushes over → HARD cap (not soft), no overage recorded.
    const over = await recordUsage(env.DB, auth, 100);
    expect(over.over_cap).toBe(true);
    expect(over.over_cap_kind).toBe('hard');
    expect(over.overage_classifications).toBe(0);
  });
});

describe('usageMiddleware via /v1 surface', () => {
  let plaintext: string;

  beforeAll(async () => {
    // A fresh user + key for an end-to-end round trip.
    const u = await SELF.fetch(`${BASE}/admin/users`, {
      method: 'POST',
      headers: adminHeaders,
      body: JSON.stringify({
        clerk_user_id: 'mw_user',
        email: 'mw@example.com',
      }),
    });
    const uid = (await u.json<{ user_id: number }>()).user_id;
    const k = await SELF.fetch(`${BASE}/admin/keys`, {
      method: 'POST',
      headers: adminHeaders,
      body: JSON.stringify({ user_id: uid }),
    });
    plaintext = (await k.json<{ plaintext: string }>()).plaintext;
  });

  it('whoami does NOT count toward usage', async () => {
    const before = await env.DB.prepare(
      `SELECT COALESCE(SUM(classifications_count), 0) AS n FROM usage_metrics WHERE api_key_id = (SELECT id FROM api_keys WHERE key_prefix = ?)`,
    ).bind(plaintext.slice(10, 18)).first<{ n: number }>();

    const res = await SELF.fetch(`${BASE}/v1/whoami`, {
      headers: { Authorization: `Bearer ${plaintext}` },
    });
    expect(res.status).toBe(200);

    const after = await env.DB.prepare(
      `SELECT COALESCE(SUM(classifications_count), 0) AS n FROM usage_metrics WHERE api_key_id = (SELECT id FROM api_keys WHERE key_prefix = ?)`,
    ).bind(plaintext.slice(10, 18)).first<{ n: number }>();
    expect(after?.n).toBe(before?.n);
  });

  it('billable POST /v1/verdicts increments usage', async () => {
    const before = await env.DB.prepare(
      `SELECT COALESCE(SUM(classifications_count), 0) AS n FROM usage_metrics WHERE api_key_id = (SELECT id FROM api_keys WHERE key_prefix = ?)`,
    ).bind(plaintext.slice(10, 18)).first<{ n: number }>();

    const res = await SELF.fetch(`${BASE}/v1/verdicts`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${plaintext}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ switch_name: 'usage_probe' }),
    });
    expect(res.status).toBe(201);

    const after = await env.DB.prepare(
      `SELECT COALESCE(SUM(classifications_count), 0) AS n FROM usage_metrics WHERE api_key_id = (SELECT id FROM api_keys WHERE key_prefix = ?)`,
    ).bind(plaintext.slice(10, 18)).first<{ n: number }>();
    expect(after?.n).toBe((before?.n ?? 0) + 1);
  });
});
