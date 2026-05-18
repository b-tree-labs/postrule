// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1
//
// Monthly classification-cap enforcement.
//
// One row per (api_key_id, "YYYY-MM") in usage_metrics. On every
// billable /v1/* request we INSERT-or-UPSERT the counter atomically
// using ON CONFLICT DO UPDATE ... RETURNING. If the post-increment
// count exceeds the tier cap, behavior depends on the tier:
//
//   * Hard-cap tiers (Free, Business+):
//       - return 429 with Retry-After until the start of next UTC month
//   * Soft-cap tiers (Pro, Scale) with overage pricing:
//       - record into overage_classifications and let the request through
//
// RPS / per-second rate limiting is intentionally deferred. For v1 we
// rely on a Cloudflare edge rate-limit rule (configurable in the
// dashboard) to bound worst-case spend; per-tier RPS enforcement lands
// in v1.1 via Durable Objects (one bucket per api_key_id).

import type { MiddlewareHandler } from 'hono';
import type { ApiEnv, AuthContext } from './auth';

/** Monthly cap per tier (null = unlimited). Values match landing/data/pricing-tiers.json. */
export const TIER_MONTHLY_CAP: Record<AuthContext['tier'], number | null> = {
  free: 10_000,
  pro: 250_000,
  scale: 5_000_000,
  business: 25_000_000,
  // `comp` matches scale-tier cap. Hard-capped (see TIER_HAS_OVERAGE) so a
  // runaway comp user surfaces a 429 instead of accruing un-billable infra
  // cost; operator can re-tier them via /admin/users/:user_id/set-tier if
  // legitimate usage justifies a bump.
  comp: 5_000_000,
};

/** Tiers whose contracts include overage billing (soft cap). */
export const TIER_HAS_OVERAGE: Record<AuthContext['tier'], boolean> = {
  free: false,
  pro: true,
  scale: true,
  business: false,
  comp: false,
};

/** "YYYY-MM" in UTC for the given Date (default: now). */
export function periodOf(d: Date = new Date()): string {
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, '0');
  return `${y}-${m}`;
}

/** Seconds until the start of next UTC month (the cap reset point). */
export function secondsUntilNextPeriod(d: Date = new Date()): number {
  const next = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1, 0, 0, 0));
  return Math.max(1, Math.ceil((next.getTime() - d.getTime()) / 1000));
}

export interface UsageState {
  classifications_count: number;
  overage_classifications: number;
  cap: number | null;
  tier: AuthContext['tier'];
  over_cap: boolean;
  over_cap_kind: 'hard' | 'soft' | null;
}

/**
 * Atomically increment usage for the current period and return the new
 * counts plus a precomputed enforcement decision.
 *
 * **Counter is the authoritative cap ledger** (architecture choice
 * confirmed 2026-05-11 from chaos finding #1, PR #42 §3): the
 * usage_metrics increment and the downstream verdicts INSERT in
 * verdicts.ts are NOT in a shared D1 transaction. If D1 fails between
 * them, the counter advances by 1 with no corresponding verdict row.
 * On retry, the request_id idempotency check on verdicts doesn't catch
 * this — the original verdict never landed.
 *
 * The trade-off: wrapping both in a D1 batch would lose the early-cap-
 * reject short-circuit below (every request would pay the verdict-prep
 * cost even when usageMiddleware would 429 it). That ~1ms hot-path
 * regression is worse for the median customer than the alternative — a
 * few "lost" verdict rows under D1 stress, where the counter still
 * counted them. The customer was billed for those verdicts; the audit
 * chain is the cohort artifact, not the per-row source of truth.
 *
 * Revisit post-launch if we see real D1 transient rates >0.1%, or if a
 * HIPAA-grade customer needs row-level atomicity. v1.1 idempotency-on-
 * retry-via-request_id-history is the cleaner long-term fix.
 */
export async function recordUsage(
  db: D1Database,
  auth: AuthContext,
  units = 1,
  now: Date = new Date(),
): Promise<UsageState> {
  const period = periodOf(now);
  const cap = TIER_MONTHLY_CAP[auth.tier];

  const row = await db
    .prepare(
      `INSERT INTO usage_metrics (api_key_id, period_start, classifications_count)
       VALUES (?, ?, ?)
       ON CONFLICT(api_key_id, period_start) DO UPDATE SET
         classifications_count = classifications_count + excluded.classifications_count,
         updated_at = datetime('now')
       RETURNING classifications_count, overage_classifications`,
    )
    .bind(auth.api_key_id, period, units)
    .first<{ classifications_count: number; overage_classifications: number }>();

  if (!row) {
    throw new Error('usage upsert returned no row');
  }

  const overCap = cap !== null && row.classifications_count > cap;
  const kind: UsageState['over_cap_kind'] =
    !overCap ? null : TIER_HAS_OVERAGE[auth.tier] ? 'soft' : 'hard';

  // Soft-cap: record the over-cap delta separately so billing can pull it.
  if (overCap && kind === 'soft' && cap !== null) {
    const overage = Math.min(units, row.classifications_count - cap);
    if (overage > 0) {
      await db
        .prepare(
          `UPDATE usage_metrics
              SET overage_classifications = overage_classifications + ?
            WHERE api_key_id = ? AND period_start = ?`,
        )
        .bind(overage, auth.api_key_id, period)
        .run();
      row.overage_classifications += overage;
    }
  }

  return {
    classifications_count: row.classifications_count,
    overage_classifications: row.overage_classifications,
    cap,
    tier: auth.tier,
    over_cap: overCap,
    over_cap_kind: kind,
  };
}

/**
 * Hono middleware: increment usage, enforce hard caps, attach state to
 * c.var.usage. Mount AFTER authMiddleware on billable /v1/* routes.
 */
export const usageMiddleware = (): MiddlewareHandler<{ Bindings: ApiEnv }> =>
  async (c, next) => {
    const auth = c.get('auth');
    if (!auth) {
      throw new Error('usageMiddleware called before authMiddleware');
    }

    const state = await recordUsage(c.env.DB, auth);
    c.set('usage', state);

    if (state.over_cap && state.over_cap_kind === 'hard') {
      const retryAfter = secondsUntilNextPeriod();
      c.header('Retry-After', String(retryAfter));
      return c.json(
        {
          error: 'monthly_cap_exceeded',
          tier: state.tier,
          cap: state.cap,
          used: state.classifications_count,
          retry_after_seconds: retryAfter,
        },
        429,
      );
    }

    await next();
  };

declare module 'hono' {
  interface ContextVariableMap {
    usage: UsageState;
  }
}
