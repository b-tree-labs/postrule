// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1
//
// Auth middleware. Extracts Bearer prul_live_… → resolves to user +
// tier via D1 lookup → attaches to Hono context for downstream handlers.

import type { Context, MiddlewareHandler } from 'hono';
import { hashKey, parseBearer } from './keys';

export interface AuthContext {
  user_id: number;
  api_key_id: number;
  // `comp` is the no-charge partner / internal-use tier. Same wire shape
  // as a paid tier; differs only in that no Stripe subscription backs it
  // and its caps are set by operator decision (currently scale-equivalent).
  // Provisioned via POST /admin/users/:user_id/set-tier, never via webhook.
  tier: 'free' | 'pro' | 'scale' | 'business' | 'comp';
  account_hash: string;
  rate_limit_rps: number;
}

export interface ApiEnv {
  DB: D1Database;
  ENVIRONMENT: string;
  API_KEY_PEPPER: string;
}

declare module 'hono' {
  interface ContextVariableMap {
    auth: AuthContext;
  }
}

const TIER_DEFAULT_RPS: Record<AuthContext['tier'], number> = {
  free: 10,
  pro: 50,
  scale: 200,
  business: 1000,
  comp: 200,
};

/**
 * Authenticate the request. On success, attach AuthContext to c.var.auth
 * and continue. On failure, return 401.
 *
 * Performance: one D1 indexed lookup per request (idx_api_keys_hash_active
 * is a partial index excluding revoked rows). HMAC computation is <0.1ms.
 */
export const authMiddleware = (): MiddlewareHandler<{ Bindings: ApiEnv }> =>
  async (c, next) => {
    const bearer = parseBearer(c.req.header('Authorization') ?? null);
    if (!bearer) {
      return c.json({ error: 'missing_or_malformed_bearer' }, 401);
    }

    const pepper = c.env.API_KEY_PEPPER;
    if (!pepper) {
      console.error('API_KEY_PEPPER secret not configured');
      return c.json({ error: 'server_misconfigured' }, 500);
    }

    const hash = await hashKey(bearer, pepper);

    const row = await c.env.DB.prepare(
      `SELECT k.id AS api_key_id,
              k.user_id,
              k.rate_limit_rps_override,
              u.current_tier AS tier,
              u.account_hash
         FROM api_keys k
         JOIN users u ON u.id = k.user_id
        WHERE k.key_hash = ?
          AND k.revoked_at IS NULL
        LIMIT 1`,
    )
      .bind(hash)
      .first<{
        api_key_id: number;
        user_id: number;
        rate_limit_rps_override: number | null;
        tier: AuthContext['tier'];
        account_hash: string;
      }>();

    if (!row) {
      return c.json({ error: 'invalid_or_revoked_key' }, 401);
    }

    const tier = row.tier;
    if (!(tier in TIER_DEFAULT_RPS)) {
      console.error(`unknown tier "${tier}" for user ${row.user_id}`);
      return c.json({ error: 'unknown_tier' }, 500);
    }

    const auth: AuthContext = {
      user_id: row.user_id,
      api_key_id: row.api_key_id,
      tier,
      account_hash: row.account_hash,
      rate_limit_rps: row.rate_limit_rps_override ?? TIER_DEFAULT_RPS[tier],
    };

    c.set('auth', auth);

    // Fire-and-forget last_used_at update; doesn't block the response.
    c.executionCtx.waitUntil(
      c.env.DB.prepare(`UPDATE api_keys SET last_used_at = datetime('now') WHERE id = ?`)
        .bind(auth.api_key_id)
        .run(),
    );

    await next();
  };

/**
 * Helper for handlers — pulls AuthContext from Hono context. Throws if
 * called outside a route protected by authMiddleware (programming error).
 */
export function requireAuth(c: Context): AuthContext {
  const auth = c.get('auth');
  if (!auth) {
    throw new Error('requireAuth called on unprotected route');
  }
  return auth as AuthContext;
}
