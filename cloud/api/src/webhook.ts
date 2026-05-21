// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1
//
// Stripe webhook receiver. Mounted at POST /webhook/stripe.
//
// Auth is via Stripe-Signature header verification (HMAC-SHA-256 with
// the shared whsec_… secret). Replay-safe: every mutating event we
// care about includes the Stripe event_id which we record in
// subscriptions.last_event_id; duplicate deliveries are no-ops.
//
// Events handled:
//   customer.subscription.created   → subscription row + users.current_tier upgrade
//   customer.subscription.updated   → status / period change, possibly tier change
//   customer.subscription.deleted   → set status='canceled', drop tier to 'free'
//
// Mapping Stripe price → Postrule tier: we look up the price's metadata.tier_id
// (set by scripts/sync-stripe-products.ts at price-creation time).

import { Hono } from 'hono';
import Stripe from 'stripe';
import type { ApiEnv, AuthContext } from './auth';

export interface WebhookEnv extends ApiEnv {
  STRIPE_SECRET_KEY: string;
  STRIPE_WEBHOOK_SECRET: string;
}

// Map Stripe price metadata tier_id (lookup_key id) → users.current_tier value.
// Both the post-2026-05-11 tier ids (`pro`, `scale`, `business`) and the
// pre-restructure ids (`hosted_pro`, `hosted_scale`, `hosted_business`)
// are accepted here so existing Stripe products whose metadata still
// reads `tier_id: hosted_*` continue to resolve correctly. Sync the
// products via cloud/api/scripts/sync-stripe-products.ts to refresh
// metadata to the new short ids.
const TIER_MAP: Record<string, AuthContext['tier']> = {
  pro: 'pro',
  scale: 'scale',
  business: 'business',
  hosted_pro: 'pro',
  hosted_scale: 'scale',
  hosted_business: 'business',
};

export const webhook = new Hono<{ Bindings: WebhookEnv }>();

webhook.post('/stripe', async (c) => {
  const sig = c.req.header('stripe-signature');
  if (!sig) return c.json({ error: 'missing_signature' }, 400);

  const raw = await c.req.text();
  const stripe = new Stripe(c.env.STRIPE_SECRET_KEY, {
    httpClient: Stripe.createFetchHttpClient(),
  });

  let event: Stripe.Event;
  try {
    event = await stripe.webhooks.constructEventAsync(
      raw,
      sig,
      c.env.STRIPE_WEBHOOK_SECRET,
    );
  } catch (e) {
    console.error('webhook signature verification failed:', e);
    return c.json({ error: 'invalid_signature' }, 400);
  }

  console.log(`stripe webhook: ${event.type} (id=${event.id})`);

  switch (event.type) {
    case 'customer.subscription.created':
    case 'customer.subscription.updated':
    case 'customer.subscription.deleted': {
      const sub = event.data.object as Stripe.Subscription;
      await handleSubscriptionEvent(c.env.DB, event, sub);
      break;
    }
    default:
      // Stripe sends many events; we only care about subscription state.
      break;
  }

  return c.json({ received: true });
});

async function handleSubscriptionEvent(
  db: D1Database,
  event: Stripe.Event,
  sub: Stripe.Subscription,
) {
  const customerId = typeof sub.customer === 'string' ? sub.customer : sub.customer.id;

  // Resolve the user. We prefer subscription_data.metadata.postrule_user_id
  // (set by the dashboard checkout route at session-create time) because
  // stripe_customer_id is only persisted on the users row after a webhook
  // round-trip — so on the *first* customer.subscription.created event for
  // a new subscriber, the stripe_customer_id column is still NULL. Without
  // the metadata path, every first-checkout webhook silently no-ops with
  // "subscription event for unknown customer", leaving the user stuck on
  // free even though Stripe collected payment. Fall back to the
  // stripe_customer_id lookup for subscriptions created outside our
  // checkout flow (manual Stripe Dashboard creations, imports, etc.).
  let userId: number | null = null;
  const metaUserId = sub.metadata?.postrule_user_id;
  if (metaUserId) {
    const n = parseInt(metaUserId, 10);
    if (Number.isFinite(n) && n > 0) userId = n;
  }
  if (!userId) {
    const userRow = await db
      .prepare(`SELECT id FROM users WHERE stripe_customer_id = ? LIMIT 1`)
      .bind(customerId)
      .first<{ id: number }>();
    if (userRow) userId = userRow.id;
  }

  if (!userId) {
    console.warn(`subscription event for unknown customer ${customerId}`);
    return;
  }

  // Persist stripe_customer_id back to the users row on first touch so
  // subsequent events, customer-portal sessions, and admin tools can
  // resolve the user without relying on subscription metadata.
  await db
    .prepare(
      `UPDATE users SET stripe_customer_id = ?, updated_at = datetime('now')
        WHERE id = ? AND (stripe_customer_id IS NULL OR stripe_customer_id != ?)`,
    )
    .bind(customerId, userId, customerId)
    .run();

  // Idempotency: if last_event_id already matches, skip.
  const existing = await db
    .prepare(
      `SELECT id, last_event_id FROM subscriptions WHERE stripe_subscription_id = ? LIMIT 1`,
    )
    .bind(sub.id)
    .first<{ id: number; last_event_id: string | null }>();
  if (existing?.last_event_id === event.id) {
    console.log(`webhook idempotent skip (event ${event.id} already applied)`);
    return;
  }

  // Determine the tier and period from the first item. The subscription
  // event already embeds the full price object on item.price, so we don't
  // need a separate stripe.prices.retrieve round-trip — that would also
  // cross-fault if the Worker's secret key is for a different sandbox
  // than the one that issued the price (e.g. after rolling test keys).
  // Stripe API 2025+ moved current_period_{start,end} from the Subscription
  // onto each SubscriptionItem; we read item-level fields and treat the
  // subscription as having a single billing item (which our Pro / Scale /
  // Business products do).
  const item = sub.items.data[0];
  let tier: AuthContext['tier'] = 'free';
  const price = item?.price;
  if (price) {
    // Tier identifier sources, in priority order:
    //  1. price.metadata.tier_id — canonical, written by
    //     scripts/sync-stripe-products.ts as "hosted_pro" / "hosted_scale" /
    //     "hosted_business". Always matches TIER_MAP directly.
    //  2. price.lookup_key — UI-facing identifier shaped like
    //     "<brand>_hosted_<tier>_monthly_usd". May carry the legacy
    //     `dendra_` prefix on prices created before the 2026-05 brand
    //     transition; new prices use `postrule_`. We strip either prefix
    //     and the `_monthly_usd` suffix before lookup.
    const tierIdFromMetadata =
      typeof price.metadata?.tier_id === 'string' ? price.metadata.tier_id : null;
    const tierIdFromLookupKey = price.lookup_key
      ? price.lookup_key.replace(/^(postrule|dendra)_/, '').replace(/_monthly_usd$/, '')
      : null;
    const productLookup = tierIdFromMetadata ?? tierIdFromLookupKey;
    if (productLookup && TIER_MAP[productLookup]) {
      tier = TIER_MAP[productLookup]!;
    }
  }

  const status = sub.status;
  const isActive = status === 'active' || status === 'trialing';
  const effectiveTier = isActive ? tier : 'free';

  const periodStart = item?.current_period_start
    ? new Date(item.current_period_start * 1000).toISOString()
    : new Date().toISOString();
  const periodEnd = item?.current_period_end
    ? new Date(item.current_period_end * 1000).toISOString()
    : new Date().toISOString();

  await db
    .prepare(
      `INSERT INTO subscriptions
         (user_id, stripe_subscription_id, tier, status,
          current_period_start, current_period_end, last_event_id)
       VALUES (?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT(stripe_subscription_id) DO UPDATE SET
         tier = excluded.tier,
         status = excluded.status,
         current_period_start = excluded.current_period_start,
         current_period_end = excluded.current_period_end,
         last_event_id = excluded.last_event_id,
         updated_at = datetime('now')`,
    )
    .bind(userId, sub.id, tier, status, periodStart, periodEnd, event.id)
    .run();

  await db
    .prepare(`UPDATE users SET current_tier = ?, updated_at = datetime('now') WHERE id = ?`)
    .bind(effectiveTier, userId)
    .run();

  console.log(
    `applied ${event.type} for user=${userId} sub=${sub.id} tier=${effectiveTier}`,
  );
}
