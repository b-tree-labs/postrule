// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1
//
// POST /api/billing/checkout
//
// Creates a Stripe Checkout session for the requested tier and returns
// the redirect URL. The dashboard billing page hits this then sends the
// browser to session.url; Stripe handles the rest. After success Stripe
// redirects back to /dashboard/billing?status=success&session_id=…,
// where we read the session, persist stripe_customer_id on the user row,
// and let the webhook receiver complete the subscription bookkeeping.

import { NextRequest, NextResponse } from "next/server";
import { auth, currentUser } from "@clerk/nextjs/server";
import { upsertUser } from "../../../../lib/postrule-api";
import { stripe, priceIdForTier, checkoutReturnUrls } from "../../../../lib/stripe";


// Tier ids mirror landing/data/pricing-tiers.json `id` fields (post
// 2026-05-11 pricing restructure). The pre-restructure ids
// (`hosted_pro`/`hosted_scale`/`hosted_business`) are kept here as
// transitional aliases until the dashboard build no longer references
// them anywhere.
const ALLOWED_TIERS = new Set([
  "starter",
  "pro",
  "scale",
  "business",
  "hosted_starter",
  "hosted_pro",
  "hosted_scale",
  "hosted_business",
]);

export async function POST(req: NextRequest) {
  try {
    const { userId } = await auth();
    if (!userId) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
    const u = await currentUser();
    const email = u?.emailAddresses?.[0]?.emailAddress;
    if (!email) return NextResponse.json({ error: "no_email" }, { status: 400 });

    const body = (await req.json().catch(() => ({}))) as { tier_id?: string };
    if (!body.tier_id || !ALLOWED_TIERS.has(body.tier_id)) {
      return NextResponse.json({ error: "invalid_tier_id" }, { status: 400 });
    }

    const postruleUser = await upsertUser(userId, email);
    const baseUrl = req.nextUrl.origin;
    const session = await stripe().checkout.sessions.create({
      mode: "subscription",
      payment_method_types: ["card"],
      line_items: [{ price: priceIdForTier(body.tier_id), quantity: 1 }],
      customer_email: email,
      // Lets Stripe show a "Have a promo code?" field on the Checkout
      // page. We use this for the FRIEND2026 trial-discount code during
      // shadow launch. Stripe handles redemption + cap accounting.
      allow_promotion_codes: true,
      // Bind subscription rows back to our user row via Stripe metadata —
      // the webhook will key off subscription.metadata or look up by
      // stripe_customer_id (set on the user row at first checkout).
      subscription_data: {
        metadata: {
          postrule_user_id: String(postruleUser.user_id),
          postrule_tier_id: body.tier_id,
        },
      },
      // Stripe shows a "I agree to the Terms of Service" checkbox at
      // checkout when consent_collection.terms_of_service = "required".
      // The terms URL itself is configured in the Stripe Dashboard
      // (Settings → Public details → Terms of service URL).
      consent_collection: { terms_of_service: "required" },
      ...checkoutReturnUrls(baseUrl),
    });

    return NextResponse.json({ url: session.url });
  } catch (e) {
    console.error("POST /api/billing/checkout", e);
    return NextResponse.json({ error: "internal_error" }, { status: 500 });
  }
}
