// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1
//
// Idempotent Stripe product + price sync.
//
// Usage (from cloud/api/):
//   STRIPE_SECRET_KEY=sk_test_… npx tsx scripts/sync-stripe-products.ts
//
// What it does, per tier in landing/data/pricing-tiers.json with a
// stripe_product_lookup_key set:
//   1. find_or_create the Product by lookup_key
//   2. find_or_create a recurring Price at the configured monthly USD
//   3. set the price's lookup_key + metadata.tier_id
//   4. print all (tier → price_id) mappings as env-var-ready exports
//
// Safe to run repeatedly — uses search-by-lookup_key, not blind create.
// Pricing source of truth is the JSON file; this script reflects it.

import Stripe from 'stripe';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const STRIPE_KEY = process.env.STRIPE_SECRET_KEY;
if (!STRIPE_KEY) {
  console.error('STRIPE_SECRET_KEY env var is required');
  process.exit(1);
}

const stripe = new Stripe(STRIPE_KEY);

interface Tier {
  id: string;
  label: string;
  price_per_month_usd: number | null;
  stripe_product_lookup_key?: string;
  classifications_per_month: number | null;
}

// __dirname is cloud/api/scripts/; go up three to repo root.
const tiersPath = resolve(__dirname, '../../../landing/data/pricing-tiers.json');
const all = JSON.parse(readFileSync(tiersPath, 'utf-8')) as { tiers: Tier[] };

const billable = all.tiers.filter(
  (t) =>
    t.stripe_product_lookup_key && t.price_per_month_usd && t.price_per_month_usd > 0,
);

async function findOrCreateProduct(t: Tier): Promise<Stripe.Product> {
  const search = await stripe.products.search({
    query: `metadata['lookup_key']:'${t.stripe_product_lookup_key}'`,
  });
  if (search.data.length > 0) {
    console.log(`  ↻  found existing product ${search.data[0]!.id}`);
    return search.data[0]!;
  }
  const created = await stripe.products.create({
    name: t.label,
    metadata: {
      lookup_key: t.stripe_product_lookup_key!,
      tier_id: t.id,
      monthly_classification_cap: String(t.classifications_per_month ?? 'unlimited'),
    },
  });
  console.log(`  +  created product ${created.id}`);
  return created;
}

async function findOrCreatePrice(t: Tier, productId: string): Promise<Stripe.Price> {
  const lookupKey = `${t.stripe_product_lookup_key}_monthly_usd`;
  const search = await stripe.prices.list({
    lookup_keys: [lookupKey],
    active: true,
    limit: 1,
  });
  if (search.data.length > 0) {
    const existing = search.data[0]!;
    if (existing.unit_amount === Math.round(t.price_per_month_usd! * 100)) {
      console.log(`  ↻  found existing price ${existing.id} ($${t.price_per_month_usd}/mo)`);
      return existing;
    }
    // Price changed: deactivate old, create new (Stripe prices are immutable on amount).
    await stripe.prices.update(existing.id, { active: false });
    console.log(`  -  deactivated stale price ${existing.id}`);
  }
  const created = await stripe.prices.create({
    product: productId,
    unit_amount: Math.round(t.price_per_month_usd! * 100),
    currency: 'usd',
    recurring: { interval: 'month' },
    lookup_key: lookupKey,
    metadata: { tier_id: t.id },
  });
  console.log(`  +  created price ${created.id} ($${t.price_per_month_usd}/mo)`);
  return created;
}

async function main() {
  console.log(`Syncing ${billable.length} billable tiers to Stripe...`);
  const out: Record<string, string> = {};
  for (const t of billable) {
    console.log(`\n# ${t.label} (${t.id})`);
    const product = await findOrCreateProduct(t);
    const price = await findOrCreatePrice(t, product.id);
    out[t.id] = price.id;
  }
  console.log('\n--- env-var snippets ---');
  for (const [tierId, priceId] of Object.entries(out)) {
    const envName = `STRIPE_PRICE_ID_${tierId.toUpperCase()}`;
    console.log(`${envName}=${priceId}`);
  }
  console.log('\nPaste those into:');
  console.log('  - cloud/dashboard env (Cloudflare Pages → Settings → Environment variables)');
  console.log('  - cloud/api wrangler secrets, if the api Worker needs to read them too');
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
