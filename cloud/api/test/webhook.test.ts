// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1
//
// Stripe webhook receiver tests.
//
// Coverage map (each region addresses a specific class of past bug or a
// specific tier-mapping responsibility):
//
//   request-shape           — signature presence + validity, unknown event
//                             types, unknown customers (no-op semantics)
//   user resolution         — metadata.postrule_user_id (primary, regression
//                             for the 2026-05-19 "stripe_customer_id is
//                             never written" bug); stripe_customer_id
//                             fallback (subs created outside our checkout);
//                             write-back on first event
//   tier resolution         — every canonical tier (starter, pro, scale,
//                             business) via metadata.tier_id (canonical
//                             source); lookup_key with postrule_ prefix
//                             (current sync output) and dendra_ prefix
//                             (legacy from the pre-2026-05 brand transition);
//                             tier_id wins over lookup_key when both present
//   lifecycle               — created (upgrade), updated (tier change),
//                             deleted (drop to free); inactive status
//                             (canceled / past_due) → effectiveTier=free
//                             regardless of price-tier
//   idempotency             — replaying the same event_id is a no-op
//                             (subscriptions.last_event_id gate)
//
// All synthetic events are signed with the same whsec_ that the Worker is
// configured with via the cloudflare:test miniflare binding, exercising
// the constructEventAsync verification path.
//
// Each test that mutates D1 uses unique customer / subscription / event ids
// so vitest's sequential execution doesn't cross-contaminate state.

import { describe, it, expect, beforeAll } from 'vitest';
import { env, SELF } from 'cloudflare:test';
import Stripe from 'stripe';
import migration0001 from '../../collector/migrations/0001_initial.sql?raw';
import migration0002 from '../../collector/migrations/0002_leads.sql?raw';
import migration0003 from '../../collector/migrations/0003_saas.sql?raw';

const SERVICE_TOKEN = 'test-service-token-for-dashboard';
const WEBHOOK_SECRET = 'whsec_dummy'; // pragma: allowlist secret
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
      if (!String(e).includes('already exists')) throw e;
    }
  }
}

beforeAll(async () => {
  await applySql(migration0001);
  await applySql(migration0002);
  await applySql(migration0003);
});

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

/** Build a signed Stripe webhook request matching the configured whsec_. */
async function signedRequest(payloadObj: object, secret = WEBHOOK_SECRET): Promise<RequestInit> {
  const payload = JSON.stringify(payloadObj);
  const stripe = new Stripe('sk_test_dummy', {
    httpClient: Stripe.createFetchHttpClient(),
  });
  // Async variant: SubtleCrypto-backed signing in Workers context.
  const header = await stripe.webhooks.generateTestHeaderStringAsync({
    payload,
    secret,
    timestamp: Math.floor(Date.now() / 1000),
  });
  return {
    method: 'POST',
    headers: { 'stripe-signature': header, 'Content-Type': 'application/json' },
    body: payload,
  };
}

interface SubEventOpts {
  eventId: string;
  type: 'customer.subscription.created' | 'customer.subscription.updated' | 'customer.subscription.deleted';
  subId: string;
  customerId: string;
  status?: 'active' | 'trialing' | 'canceled' | 'past_due' | 'incomplete';
  metadata?: Record<string, string>;
  priceLookupKey?: string | null;
  priceMetadata?: Record<string, string>;
  periodStart?: number;
  periodEnd?: number;
}

/** Build a Stripe.Event payload shaped like a real customer.subscription.* delivery. */
function subscriptionEvent(opts: SubEventOpts): object {
  const now = Math.floor(Date.now() / 1000);
  const periodStart = opts.periodStart ?? now;
  const periodEnd = opts.periodEnd ?? now + 30 * 86400;
  return {
    id: opts.eventId,
    object: 'event',
    type: opts.type,
    data: {
      object: {
        id: opts.subId,
        object: 'subscription',
        customer: opts.customerId,
        status: opts.status ?? 'active',
        metadata: opts.metadata ?? {},
        items: {
          object: 'list',
          data: [
            {
              id: `si_${opts.subId.slice(-8)}`,
              object: 'subscription_item',
              current_period_start: periodStart,
              current_period_end: periodEnd,
              price: {
                id: `price_${opts.subId.slice(-8)}`,
                object: 'price',
                lookup_key: opts.priceLookupKey ?? null,
                metadata: opts.priceMetadata ?? {},
              },
            },
          ],
        },
      },
    },
  };
}

/** Provision a user via /admin/users, optionally pre-linking a stripe_customer_id. */
async function createUser(opts: {
  clerkId: string;
  email: string;
  stripeCustomerId?: string;
}): Promise<number> {
  const u = await SELF.fetch(`${BASE}/admin/users`, {
    method: 'POST',
    headers: adminHeaders,
    body: JSON.stringify({ clerk_user_id: opts.clerkId, email: opts.email }),
  });
  const userId = (await u.json<{ user_id: number }>()).user_id;
  if (opts.stripeCustomerId) {
    await env.DB.prepare(`UPDATE users SET stripe_customer_id = ? WHERE id = ?`)
      .bind(opts.stripeCustomerId, userId)
      .run();
  }
  return userId;
}

async function getUser(userId: number) {
  return env.DB.prepare(
    `SELECT id, email, current_tier, stripe_customer_id FROM users WHERE id = ?`,
  )
    .bind(userId)
    .first<{
      id: number;
      email: string;
      current_tier: string;
      stripe_customer_id: string | null;
    }>();
}

async function getSubscription(subId: string) {
  return env.DB.prepare(
    `SELECT id, user_id, stripe_subscription_id, tier, status, last_event_id
     FROM subscriptions WHERE stripe_subscription_id = ?`,
  )
    .bind(subId)
    .first<{
      id: number;
      user_id: number;
      stripe_subscription_id: string;
      tier: string;
      status: string;
      last_event_id: string | null;
    }>();
}

// ---------------------------------------------------------------------------
// tests
// ---------------------------------------------------------------------------

describe('webhook /webhook/stripe', () => {
  let baselineUserId: number;

  beforeAll(async () => {
    baselineUserId = await createUser({
      clerkId: 'wh_user',
      email: 'wh@example.com',
      stripeCustomerId: 'cus_test_001',
    });
  });

  // -------------------------------------------------------------------------
  // request-shape
  // -------------------------------------------------------------------------

  describe('request shape', () => {
    it('rejects requests without stripe-signature', async () => {
      const res = await SELF.fetch(`${BASE}/webhook/stripe`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      expect(res.status).toBe(400);
    });

    it('rejects requests with bad signature', async () => {
      const res = await SELF.fetch(`${BASE}/webhook/stripe`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'stripe-signature': 'bogus' },
        body: '{}',
      });
      expect(res.status).toBe(400);
    });

    it('ignores unrelated events with 200', async () => {
      const event = {
        id: 'evt_test_shape_1',
        object: 'event',
        type: 'invoice.paid',
        data: { object: {} },
      };
      const res = await SELF.fetch(`${BASE}/webhook/stripe`, await signedRequest(event));
      expect(res.status).toBe(200);
    });

    it('customer.subscription.created with unknown customer + no metadata is a no-op (200, no subscriptions row)', async () => {
      const event = subscriptionEvent({
        eventId: 'evt_test_unknown_cust',
        type: 'customer.subscription.created',
        subId: 'sub_unknown',
        customerId: 'cus_does_not_exist',
        priceMetadata: { tier_id: 'hosted_pro' },
      });
      const res = await SELF.fetch(`${BASE}/webhook/stripe`, await signedRequest(event));
      expect(res.status).toBe(200);
      const row = await getSubscription('sub_unknown');
      expect(row).toBeNull();
    });
  });

  // -------------------------------------------------------------------------
  // user resolution
  // -------------------------------------------------------------------------

  describe('user resolution', () => {
    it('resolves via metadata.postrule_user_id when stripe_customer_id is NULL (regression: pre-2026-05-19 first-checkout bug)', async () => {
      const userId = await createUser({
        clerkId: 'wh_meta_only',
        email: 'meta-only@example.com',
        // stripe_customer_id intentionally NOT set — this is the path
        // every first-checkout webhook hits before the customer_id
        // write-back lands.
      });
      const event = subscriptionEvent({
        eventId: 'evt_meta_only_1',
        type: 'customer.subscription.created',
        subId: 'sub_meta_only',
        customerId: 'cus_meta_only',
        metadata: { postrule_user_id: String(userId), postrule_tier_id: 'pro' },
        priceMetadata: { tier_id: 'hosted_pro' },
      });
      const res = await SELF.fetch(`${BASE}/webhook/stripe`, await signedRequest(event));
      expect(res.status).toBe(200);

      const sub = await getSubscription('sub_meta_only');
      expect(sub).not.toBeNull();
      expect(sub!.user_id).toBe(userId);
      expect(sub!.tier).toBe('pro');

      const u = await getUser(userId);
      expect(u!.current_tier).toBe('pro');
      // stripe_customer_id should now be persisted via the first-event write-back.
      expect(u!.stripe_customer_id).toBe('cus_meta_only');
    });

    it('resolves via stripe_customer_id fallback when metadata.postrule_user_id is absent', async () => {
      const userId = await createUser({
        clerkId: 'wh_cust_only',
        email: 'cust-only@example.com',
        stripeCustomerId: 'cus_cust_only',
      });
      const event = subscriptionEvent({
        eventId: 'evt_cust_only_1',
        type: 'customer.subscription.created',
        subId: 'sub_cust_only',
        customerId: 'cus_cust_only',
        // no metadata.postrule_user_id — exercises the fallback path
        // used for subscriptions created outside our checkout flow
        // (manual Stripe Dashboard creation, imports, etc.).
        priceMetadata: { tier_id: 'hosted_scale' },
      });
      const res = await SELF.fetch(`${BASE}/webhook/stripe`, await signedRequest(event));
      expect(res.status).toBe(200);

      const sub = await getSubscription('sub_cust_only');
      expect(sub!.user_id).toBe(userId);
      expect(sub!.tier).toBe('scale');

      const u = await getUser(userId);
      expect(u!.current_tier).toBe('scale');
    });

    it('no-ops cleanly when both metadata and customer_id lookups fail (logs warn, returns 200)', async () => {
      const event = subscriptionEvent({
        eventId: 'evt_no_resolve',
        type: 'customer.subscription.created',
        subId: 'sub_no_resolve',
        customerId: 'cus_unresolvable',
        // No metadata, no matching stripe_customer_id anywhere in DB.
        priceMetadata: { tier_id: 'hosted_pro' },
      });
      const res = await SELF.fetch(`${BASE}/webhook/stripe`, await signedRequest(event));
      expect(res.status).toBe(200);
      const sub = await getSubscription('sub_no_resolve');
      expect(sub).toBeNull();
    });
  });

  // -------------------------------------------------------------------------
  // tier resolution
  // -------------------------------------------------------------------------

  describe('tier resolution', () => {
    it.each([
      ['starter', 'starter'],
      ['pro', 'pro'],
      ['scale', 'scale'],
      ['business', 'business'],
      ['hosted_starter', 'starter'],
      ['hosted_pro', 'pro'],
      ['hosted_scale', 'scale'],
      ['hosted_business', 'business'],
    ] as const)('metadata.tier_id="%s" → tier="%s"', async (tierId, expected) => {
      const userId = await createUser({
        clerkId: `wh_tier_${tierId}`,
        email: `tier-${tierId}@example.com`,
      });
      const event = subscriptionEvent({
        eventId: `evt_tier_${tierId}`,
        type: 'customer.subscription.created',
        subId: `sub_tier_${tierId}`,
        customerId: `cus_tier_${tierId}`,
        metadata: { postrule_user_id: String(userId) },
        priceMetadata: { tier_id: tierId },
      });
      const res = await SELF.fetch(`${BASE}/webhook/stripe`, await signedRequest(event));
      expect(res.status).toBe(200);
      const sub = await getSubscription(`sub_tier_${tierId}`);
      expect(sub!.tier).toBe(expected);
      const u = await getUser(userId);
      expect(u!.current_tier).toBe(expected);
    });

    it.each([
      ['postrule_hosted_pro_monthly_usd', 'pro'],
      ['postrule_hosted_scale_monthly_usd', 'scale'],
      ['postrule_hosted_business_monthly_usd', 'business'],
      ['postrule_hosted_starter_monthly_usd', 'starter'],
      // dendra_ prefix is the legacy form from pre-2026-05 brand transition.
      // Regression for the 2026-05-19 bug where the webhook only stripped
      // postrule_ and left "dendra_hosted_scale" unmapped → defaulted to free.
      ['dendra_hosted_pro_monthly_usd', 'pro'],
      ['dendra_hosted_scale_monthly_usd', 'scale'],
      ['dendra_hosted_business_monthly_usd', 'business'],
    ] as const)('lookup_key="%s" → tier="%s"', async (lookupKey, expected) => {
      const safeKey = lookupKey.replace(/[^a-z0-9]/g, '_');
      const userId = await createUser({
        clerkId: `wh_lk_${safeKey}`,
        email: `lk-${safeKey}@example.com`,
      });
      const event = subscriptionEvent({
        eventId: `evt_lk_${safeKey}`,
        type: 'customer.subscription.created',
        subId: `sub_lk_${safeKey}`,
        customerId: `cus_lk_${safeKey}`,
        metadata: { postrule_user_id: String(userId) },
        priceLookupKey: lookupKey,
        // intentionally no priceMetadata.tier_id — forces the resolver
        // to use the lookup_key path
      });
      const res = await SELF.fetch(`${BASE}/webhook/stripe`, await signedRequest(event));
      expect(res.status).toBe(200);
      const u = await getUser(userId);
      expect(u!.current_tier).toBe(expected);
    });

    it('prefers metadata.tier_id over lookup_key when both are present', async () => {
      const userId = await createUser({
        clerkId: 'wh_tier_pref',
        email: 'tier-pref@example.com',
      });
      const event = subscriptionEvent({
        eventId: 'evt_tier_pref',
        type: 'customer.subscription.created',
        subId: 'sub_tier_pref',
        customerId: 'cus_tier_pref',
        metadata: { postrule_user_id: String(userId) },
        // lookup_key says scale but metadata.tier_id says business — the
        // canonical metadata source should win.
        priceLookupKey: 'dendra_hosted_scale_monthly_usd',
        priceMetadata: { tier_id: 'hosted_business' },
      });
      const res = await SELF.fetch(`${BASE}/webhook/stripe`, await signedRequest(event));
      expect(res.status).toBe(200);
      const u = await getUser(userId);
      expect(u!.current_tier).toBe('business');
    });

    it('falls through to tier=free when no resolvable tier identifier is present on the price', async () => {
      const userId = await createUser({
        clerkId: 'wh_no_tier',
        email: 'no-tier@example.com',
      });
      const event = subscriptionEvent({
        eventId: 'evt_no_tier',
        type: 'customer.subscription.created',
        subId: 'sub_no_tier',
        customerId: 'cus_no_tier',
        metadata: { postrule_user_id: String(userId) },
        // empty lookup_key + empty priceMetadata
      });
      const res = await SELF.fetch(`${BASE}/webhook/stripe`, await signedRequest(event));
      expect(res.status).toBe(200);
      const sub = await getSubscription('sub_no_tier');
      expect(sub!.tier).toBe('free');
      const u = await getUser(userId);
      expect(u!.current_tier).toBe('free');
    });
  });

  // -------------------------------------------------------------------------
  // lifecycle
  // -------------------------------------------------------------------------

  describe('lifecycle', () => {
    it('customer.subscription.updated applies a tier change', async () => {
      const userId = await createUser({
        clerkId: 'wh_lifecycle_upd',
        email: 'lifecycle-upd@example.com',
      });
      // initial create at pro
      await SELF.fetch(
        `${BASE}/webhook/stripe`,
        await signedRequest(
          subscriptionEvent({
            eventId: 'evt_lifecycle_upd_create',
            type: 'customer.subscription.created',
            subId: 'sub_lifecycle_upd',
            customerId: 'cus_lifecycle_upd',
            metadata: { postrule_user_id: String(userId) },
            priceMetadata: { tier_id: 'hosted_pro' },
          }),
        ),
      );
      expect((await getUser(userId))!.current_tier).toBe('pro');

      // updated to scale
      await SELF.fetch(
        `${BASE}/webhook/stripe`,
        await signedRequest(
          subscriptionEvent({
            eventId: 'evt_lifecycle_upd_update',
            type: 'customer.subscription.updated',
            subId: 'sub_lifecycle_upd',
            customerId: 'cus_lifecycle_upd',
            metadata: { postrule_user_id: String(userId) },
            priceMetadata: { tier_id: 'hosted_scale' },
          }),
        ),
      );
      expect((await getUser(userId))!.current_tier).toBe('scale');
      const sub = await getSubscription('sub_lifecycle_upd');
      expect(sub!.tier).toBe('scale');
    });

    it('customer.subscription.deleted drops users.current_tier to free', async () => {
      const userId = await createUser({
        clerkId: 'wh_lifecycle_del',
        email: 'lifecycle-del@example.com',
      });
      await SELF.fetch(
        `${BASE}/webhook/stripe`,
        await signedRequest(
          subscriptionEvent({
            eventId: 'evt_lifecycle_del_create',
            type: 'customer.subscription.created',
            subId: 'sub_lifecycle_del',
            customerId: 'cus_lifecycle_del',
            metadata: { postrule_user_id: String(userId) },
            priceMetadata: { tier_id: 'hosted_business' },
          }),
        ),
      );
      expect((await getUser(userId))!.current_tier).toBe('business');

      await SELF.fetch(
        `${BASE}/webhook/stripe`,
        await signedRequest(
          subscriptionEvent({
            eventId: 'evt_lifecycle_del_delete',
            type: 'customer.subscription.deleted',
            subId: 'sub_lifecycle_del',
            customerId: 'cus_lifecycle_del',
            status: 'canceled',
            metadata: { postrule_user_id: String(userId) },
            priceMetadata: { tier_id: 'hosted_business' },
          }),
        ),
      );
      expect((await getUser(userId))!.current_tier).toBe('free');
    });

    it.each([['canceled'], ['past_due'], ['incomplete']] as const)(
      'inactive status "%s" → effectiveTier=free regardless of price tier',
      async (status) => {
        const userId = await createUser({
          clerkId: `wh_inactive_${status}`,
          email: `inactive-${status}@example.com`,
        });
        const event = subscriptionEvent({
          eventId: `evt_inactive_${status}`,
          type: 'customer.subscription.updated',
          subId: `sub_inactive_${status}`,
          customerId: `cus_inactive_${status}`,
          status,
          metadata: { postrule_user_id: String(userId) },
          // price says scale, but status is inactive → user lands on free
          priceMetadata: { tier_id: 'hosted_scale' },
        });
        await SELF.fetch(`${BASE}/webhook/stripe`, await signedRequest(event));
        const u = await getUser(userId);
        expect(u!.current_tier).toBe('free');
        const sub = await getSubscription(`sub_inactive_${status}`);
        // subscriptions.tier stores the *target* tier (scale here) even
        // when effectiveTier=free; only users.current_tier reflects the
        // active-vs-inactive gate. Lets us light back up on next reactivation
        // event without re-resolving the price.
        expect(sub!.tier).toBe('scale');
        expect(sub!.status).toBe(status);
      },
    );
  });

  // -------------------------------------------------------------------------
  // idempotency
  // -------------------------------------------------------------------------

  describe('idempotency', () => {
    it('replaying the same event_id is a no-op (subscriptions.last_event_id gate)', async () => {
      const userId = await createUser({
        clerkId: 'wh_idem',
        email: 'idem@example.com',
      });
      const eventBody = subscriptionEvent({
        eventId: 'evt_idem_replay',
        type: 'customer.subscription.created',
        subId: 'sub_idem',
        customerId: 'cus_idem',
        metadata: { postrule_user_id: String(userId) },
        priceMetadata: { tier_id: 'hosted_pro' },
      });

      // First delivery: row appears, tier='pro'.
      const r1 = await SELF.fetch(`${BASE}/webhook/stripe`, await signedRequest(eventBody));
      expect(r1.status).toBe(200);
      const after1 = await getSubscription('sub_idem');
      expect(after1!.tier).toBe('pro');
      expect(after1!.last_event_id).toBe('evt_idem_replay');
      const updatedAt1 = await env.DB.prepare(
        `SELECT updated_at FROM subscriptions WHERE stripe_subscription_id = ?`,
      )
        .bind('sub_idem')
        .first<{ updated_at: string }>();

      // Sleep 50ms so a real timestamp delta would be observable if the
      // idempotency gate failed and we wrote again.
      await new Promise((r) => setTimeout(r, 50));

      // Replay: same event id, same body. Worker should detect via
      // last_event_id match and early-return without mutating.
      const r2 = await SELF.fetch(`${BASE}/webhook/stripe`, await signedRequest(eventBody));
      expect(r2.status).toBe(200);
      const after2 = await env.DB.prepare(
        `SELECT updated_at FROM subscriptions WHERE stripe_subscription_id = ?`,
      )
        .bind('sub_idem')
        .first<{ updated_at: string }>();
      expect(after2!.updated_at).toBe(updatedAt1!.updated_at);
    });
  });
});
