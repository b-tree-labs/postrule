// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1
//
// POST /api/billing/portal
//
// Creates a Stripe Customer Portal session. Used by the dashboard's
// "Manage subscription" button — Stripe's hosted portal handles
// upgrade/downgrade/cancel/payment-method updates without us building UI.

import { NextRequest, NextResponse } from "next/server";
import { auth, currentUser } from "@clerk/nextjs/server";
import { upsertUser } from "../../../../lib/postrule-api";
import { stripe } from "../../../../lib/stripe";


export async function POST(req: NextRequest) {
  try {
    const { userId } = await auth();
    if (!userId) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
    const u = await currentUser();
    const email = u?.emailAddresses?.[0]?.emailAddress;
    if (!email) return NextResponse.json({ error: "no_email" }, { status: 400 });

    const postruleUser = await upsertUser(userId, email);

    // Fetch the user's stripe_customer_id from the api Worker's admin surface.
    // (The portal session needs an existing customer; we set this on first
    // checkout via the success_url handler in week-2 day-9 follow-up.)
    // For now we look up by email — Stripe will return the customer if they
    // exist, otherwise the portal call errors and the user has to subscribe first.
    const customers = await stripe().customers.list({ email, limit: 1 });
    const customer = customers.data[0];
    if (!customer) {
      return NextResponse.json(
        { error: "no_subscription", message: "Subscribe first to access the billing portal." },
        { status: 404 },
      );
    }

    const baseUrl = req.nextUrl.origin;
    const session = await stripe().billingPortal.sessions.create({
      customer: customer.id,
      return_url: `${baseUrl}/dashboard/billing`,
    });

    return NextResponse.json({ url: session.url });
  } catch (e) {
    console.error("POST /api/billing/portal", e);
    return NextResponse.json({ error: "internal_error" }, { status: 500 });
  }
}
