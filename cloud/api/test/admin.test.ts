// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1
//
// Admin endpoint tests. Uses the in-Workers vitest pool with a real
// D1 binding; migration 0003 is applied at suite start.

import { describe, it, expect, beforeAll } from 'vitest';
import { env, SELF } from 'cloudflare:test';
import migration0001 from '../../collector/migrations/0001_initial.sql?raw';
import migration0002 from '../../collector/migrations/0002_leads.sql?raw';
import migration0003 from '../../collector/migrations/0003_saas.sql?raw';
import migration0004 from '../../collector/migrations/0004_verdicts.sql?raw';
import migration0008 from '../../collector/migrations/0008_switch_archives.sql?raw';

const SERVICE_TOKEN = 'test-service-token-for-dashboard';
const BASE = 'https://api.test';

const headers = {
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
      if (!String(e).includes('already exists')) throw e;
    }
  }
}

beforeAll(async () => {
  await applySql(migration0001);
  await applySql(migration0002);
  await applySql(migration0003);
  await applySql(migration0004);
  await applySql(migration0008);
});

describe('admin: service-token auth', () => {
  it('rejects requests without X-Dashboard-Token', async () => {
    const res = await SELF.fetch(`${BASE}/admin/keys?user_id=1`);
    expect(res.status).toBe(401);
  });

  it('rejects requests with wrong token', async () => {
    const res = await SELF.fetch(`${BASE}/admin/keys?user_id=1`, {
      headers: { 'X-Dashboard-Token': 'wrong' },
    });
    expect(res.status).toBe(401);
  });

  it('accepts requests with the correct token', async () => {
    const res = await SELF.fetch(`${BASE}/admin/keys?user_id=1`, { headers });
    expect([200, 400, 404]).toContain(res.status);
  });
});

describe('admin: user upsert + key lifecycle', () => {
  let userId: number;

  it('POST /admin/users creates a new user', async () => {
    const res = await SELF.fetch(`${BASE}/admin/users`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        clerk_user_id: 'user_test_001',
        email: 'test@example.com',
      }),
    });
    expect(res.status).toBe(200);
    const body = await res.json<{ user_id: number; tier: string }>();
    expect(body.user_id).toBeGreaterThan(0);
    expect(body.tier).toBe('free');
    userId = body.user_id;
  });

  it('POST /admin/users is idempotent on the same clerk_user_id', async () => {
    const res = await SELF.fetch(`${BASE}/admin/users`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        clerk_user_id: 'user_test_001',
        email: 'test@example.com',
      }),
    });
    expect(res.status).toBe(200);
    const body = await res.json<{ user_id: number }>();
    expect(body.user_id).toBe(userId);
  });

  it('POST /admin/keys issues a new key', async () => {
    const res = await SELF.fetch(`${BASE}/admin/keys`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ user_id: userId, name: 'production' }),
    });
    expect(res.status).toBe(200);
    const body = await res.json<{
      id: number;
      plaintext: string;
      prefix: string;
      suffix: string;
      name: string;
    }>();
    expect(body.plaintext).toMatch(/^prul_live_[A-Za-z0-9]{32}$/);
    expect(body.prefix.length).toBe(8);
    expect(body.suffix.length).toBe(4);
    expect(body.name).toBe('production');
    expect(body.id).toBeGreaterThan(0);
  });

  it("GET /admin/keys lists the user's keys", async () => {
    const res = await SELF.fetch(`${BASE}/admin/keys?user_id=${userId}`, { headers });
    expect(res.status).toBe(200);
    const body = await res.json<{
      keys: Array<{ name: string | null; revoked_at: string | null }>;
    }>();
    expect(body.keys.length).toBeGreaterThanOrEqual(1);
    expect(body.keys[0]?.revoked_at).toBeNull();
  });

  it('DELETE /admin/keys/:id revokes a key', async () => {
    const issueRes = await SELF.fetch(`${BASE}/admin/keys`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ user_id: userId, name: 'revokable' }),
    });
    const issued = await issueRes.json<{ id: number }>();

    const revokeRes = await SELF.fetch(`${BASE}/admin/keys/${issued.id}`, {
      method: 'DELETE',
      headers,
      body: JSON.stringify({ user_id: userId }),
    });
    expect(revokeRes.status).toBe(200);
    const body = await revokeRes.json<{ revoked: boolean; id: number }>();
    expect(body.revoked).toBe(true);
    expect(body.id).toBe(issued.id);
  });

  it('DELETE /admin/keys/:id rejects cross-user revoke', async () => {
    const u2 = await SELF.fetch(`${BASE}/admin/users`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        clerk_user_id: 'user_test_002',
        email: 'attacker@example.com',
      }),
    });
    const u2body = await u2.json<{ user_id: number }>();

    const issued = await SELF.fetch(`${BASE}/admin/keys`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ user_id: userId }),
    });
    const k = await issued.json<{ id: number }>();

    const attackRes = await SELF.fetch(`${BASE}/admin/keys/${k.id}`, {
      method: 'DELETE',
      headers,
      body: JSON.stringify({ user_id: u2body.user_id }),
    });
    expect(attackRes.status).toBe(404);
  });

  it('POST /admin/keys rejects unknown user_id', async () => {
    const res = await SELF.fetch(`${BASE}/admin/keys`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ user_id: 99999 }),
    });
    expect(res.status).toBe(404);
  });
});

// ---------------------------------------------------------------------------
// POST /admin/users/:user_id/set-tier — operator escape hatch for
// re-tiering users without going through Stripe. Used to provision
// no-charge `comp` partner licenses.
// ---------------------------------------------------------------------------
describe('admin: POST /users/:user_id/set-tier', () => {
  let userId: number;

  beforeAll(async () => {
    const u = await SELF.fetch(`${BASE}/admin/users`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        clerk_user_id: 'user_set_tier_001',
        email: 'set-tier@example.com',
      }),
    });
    userId = (await u.json<{ user_id: number }>()).user_id;
  });

  it('sets a known tier and returns the updated user', async () => {
    const res = await SELF.fetch(`${BASE}/admin/users/${userId}/set-tier`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ tier: 'comp' }),
    });
    expect(res.status).toBe(200);
    const body = await res.json<{ user_id: number; tier: string; email: string }>();
    expect(body.user_id).toBe(userId);
    expect(body.tier).toBe('comp');
    expect(body.email).toBe('set-tier@example.com');
  });

  it('accepts every documented tier', async () => {
    for (const tier of ['free', 'pro', 'scale', 'business', 'comp']) {
      const res = await SELF.fetch(`${BASE}/admin/users/${userId}/set-tier`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ tier }),
      });
      expect(res.status).toBe(200);
      const body = await res.json<{ tier: string }>();
      expect(body.tier).toBe(tier);
    }
  });

  it('rejects an unknown tier with 400 and surfaces the valid list', async () => {
    const res = await SELF.fetch(`${BASE}/admin/users/${userId}/set-tier`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ tier: 'enterprise' }),
    });
    expect(res.status).toBe(400);
    const body = await res.json<{ error: string; valid_tiers: string[] }>();
    expect(body.error).toBe('invalid_tier');
    expect(body.valid_tiers).toEqual(['free', 'pro', 'scale', 'business', 'comp']);
  });

  it('rejects a missing tier field', async () => {
    const res = await SELF.fetch(`${BASE}/admin/users/${userId}/set-tier`, {
      method: 'POST',
      headers,
      body: JSON.stringify({}),
    });
    expect(res.status).toBe(400);
    const body = await res.json<{ error: string }>();
    expect(body.error).toBe('missing_tier');
  });

  it('rejects an invalid user_id with 400', async () => {
    const res = await SELF.fetch(`${BASE}/admin/users/not-a-number/set-tier`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ tier: 'comp' }),
    });
    expect(res.status).toBe(400);
    expect((await res.json<{ error: string }>()).error).toBe('invalid_user_id');
  });

  it('returns 404 for a user_id that does not exist', async () => {
    const res = await SELF.fetch(`${BASE}/admin/users/99999/set-tier`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ tier: 'comp' }),
    });
    expect(res.status).toBe(404);
    expect((await res.json<{ error: string }>()).error).toBe('user_not_found');
  });

  it('requires the service token', async () => {
    const res = await SELF.fetch(`${BASE}/admin/users/${userId}/set-tier`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tier: 'comp' }),
    });
    expect(res.status).toBe(401);
  });
});

// ---------------------------------------------------------------------------
// GET /admin/usage and GET /admin/verdicts/recent — dashboard root-page
// data sources. Run in their own isolated user so the counts are
// predictable regardless of test ordering.
// ---------------------------------------------------------------------------
describe('admin: dashboard root data — usage + recent verdicts', () => {
  let userId: number;
  let bearer: string;
  let apiKeyId: number;

  beforeAll(async () => {
    const u = await SELF.fetch(`${BASE}/admin/users`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        clerk_user_id: 'dashboard_root_user',
        email: 'dashboard-root@example.com',
      }),
    });
    userId = (await u.json<{ user_id: number }>()).user_id;

    const k = await SELF.fetch(`${BASE}/admin/keys`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ user_id: userId, name: 'root-test' }),
    });
    const kb = await k.json<{ id: number; plaintext: string }>();
    apiKeyId = kb.id;
    bearer = kb.plaintext;
  });

  it('GET /admin/usage returns tier + cap + zero verdicts for a fresh user', async () => {
    const res = await SELF.fetch(`${BASE}/admin/usage?user_id=${userId}`, { headers });
    expect(res.status).toBe(200);
    const body = await res.json<{
      tier: string;
      verdicts_this_period: number;
      cap: number | null;
      period_start: string;
      period_end: string;
    }>();
    expect(body.tier).toBe('free');
    expect(body.cap).toBe(10_000);
    expect(body.verdicts_this_period).toBe(0);
    // period_start is the first of this UTC month; period_end is the first of next.
    expect(body.period_start).toMatch(/T00:00:00\.000Z$/);
    expect(body.period_end).toMatch(/T00:00:00\.000Z$/);
    expect(new Date(body.period_end).getTime()).toBeGreaterThan(
      new Date(body.period_start).getTime(),
    );
  });

  it('GET /admin/usage reflects verdicts the user has posted', async () => {
    // Post 3 verdicts via the public surface (which increments usage).
    for (let i = 0; i < 3; i++) {
      const r = await SELF.fetch(`${BASE}/v1/verdicts`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${bearer}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ switch_name: 'root_probe', phase: 'P3', rule_correct: true }),
      });
      expect(r.status).toBe(201);
    }

    const res = await SELF.fetch(`${BASE}/admin/usage?user_id=${userId}`, { headers });
    expect(res.status).toBe(200);
    const body = await res.json<{ verdicts_this_period: number; cap: number }>();
    expect(body.verdicts_this_period).toBe(3);
    expect(body.cap).toBe(10_000);
  });

  it('GET /admin/usage reflects the upgraded tier when current_tier changes', async () => {
    await env.DB.prepare(`UPDATE users SET current_tier = 'pro' WHERE id = ?`)
      .bind(userId)
      .run();
    const res = await SELF.fetch(`${BASE}/admin/usage?user_id=${userId}`, { headers });
    const body = await res.json<{ tier: string; cap: number }>();
    expect(body.tier).toBe('pro');
    expect(body.cap).toBe(250_000);
    // Reset for downstream tests.
    await env.DB.prepare(`UPDATE users SET current_tier = 'free' WHERE id = ?`)
      .bind(userId)
      .run();
  });

  it('GET /admin/usage rejects missing / invalid user_id', async () => {
    expect((await SELF.fetch(`${BASE}/admin/usage`, { headers })).status).toBe(400);
    expect((await SELF.fetch(`${BASE}/admin/usage?user_id=0`, { headers })).status).toBe(400);
    expect((await SELF.fetch(`${BASE}/admin/usage?user_id=abc`, { headers })).status).toBe(400);
    expect((await SELF.fetch(`${BASE}/admin/usage?user_id=999999`, { headers })).status).toBe(404);
  });

  it('GET /admin/usage requires the service token', async () => {
    const res = await SELF.fetch(`${BASE}/admin/usage?user_id=${userId}`);
    expect(res.status).toBe(401);
  });

  it('GET /admin/verdicts/recent returns the most-recent N (newest-first)', async () => {
    // Drop a few more verdicts with distinct switch_names so we can
    // verify ordering by created_at DESC.
    for (const name of ['feed_a', 'feed_b', 'feed_c']) {
      const r = await SELF.fetch(`${BASE}/v1/verdicts`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${bearer}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ switch_name: name, phase: 'P4', ml_correct: true }),
      });
      expect(r.status).toBe(201);
    }

    const res = await SELF.fetch(
      `${BASE}/admin/verdicts/recent?user_id=${userId}&limit=5`,
      { headers },
    );
    expect(res.status).toBe(200);
    const body = await res.json<{
      verdicts: Array<{ switch_name: string; phase: string | null; created_at: string }>;
    }>();
    expect(body.verdicts.length).toBeLessThanOrEqual(5);
    expect(body.verdicts.length).toBeGreaterThanOrEqual(3);
    // Newest first: feed_c was inserted last.
    expect(body.verdicts[0].switch_name).toBe('feed_c');
  });

  it('GET /admin/verdicts/recent returns empty array for a user with no verdicts', async () => {
    const u = await SELF.fetch(`${BASE}/admin/users`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        clerk_user_id: 'empty_feed_user',
        email: 'empty-feed@example.com',
      }),
    });
    const emptyUid = (await u.json<{ user_id: number }>()).user_id;
    const res = await SELF.fetch(
      `${BASE}/admin/verdicts/recent?user_id=${emptyUid}&limit=5`,
      { headers },
    );
    expect(res.status).toBe(200);
    const body = await res.json<{ verdicts: unknown[] }>();
    expect(body.verdicts).toEqual([]);
  });

  it('GET /admin/verdicts/recent clamps limit to 50', async () => {
    const res = await SELF.fetch(
      `${BASE}/admin/verdicts/recent?user_id=${userId}&limit=9999`,
      { headers },
    );
    expect(res.status).toBe(200);
    const body = await res.json<{ verdicts: unknown[] }>();
    expect(body.verdicts.length).toBeLessThanOrEqual(50);
  });

  it('GET /admin/verdicts/recent never returns rows from another user', async () => {
    // Belt-and-suspenders: the join on api_keys.user_id should already
    // scope rows. Confirm.
    const other = await SELF.fetch(`${BASE}/admin/users`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        clerk_user_id: 'isolation_user',
        email: 'iso@example.com',
      }),
    });
    const otherUid = (await other.json<{ user_id: number }>()).user_id;

    const res = await SELF.fetch(
      `${BASE}/admin/verdicts/recent?user_id=${otherUid}&limit=10`,
      { headers },
    );
    const body = await res.json<{ verdicts: Array<{ switch_name: string }> }>();
    // Should be empty — this user has never posted a verdict.
    expect(body.verdicts.length).toBe(0);
    // Suppress unused-var lint.
    void apiKeyId;
  });
});
