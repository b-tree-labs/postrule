// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1
"use client";

import { useState } from "react";

interface PlanOption {
  tier_id: string;
  label: string;
  price: string;
  features: string;
  upgrade_summary: string;
  // 'available' = signup enabled via Stripe Checkout
  // 'coming_soon' = displayed with a waitlist CTA, no Stripe path
  // 'sales_led' = displayed with a "Talk to sales" CTA, no Stripe path
  status?: "available" | "coming_soon" | "sales_led";
  available_from?: string;
}

// Tier ids here mirror landing/data/pricing-tiers.json `id` fields. The
// Stripe-coupling layer (cloud/api/src/webhook.ts TIER_MAP,
// cloud/dashboard/lib/stripe.ts priceIdForTier, wrangler.toml
// STRIPE_PRICE_ID_<TIER>) keys off these same tier ids; rename here only
// in lockstep with those. The metering unit is "Verdicts / mo" — a verdict
// is one classification Postrule logs to your account; it feeds your report
// card and the cohort.
//
// 2026-05-20 pricing-v2 marketing-first rollout: Starter ($29) added as
// coming_soon (waitlist CTA, no Stripe product yet). Hosted classifier API
// quota lines surfaced on every paid tier as "included on rollout (Q3
// 2026)" — actual quota tracking ships when the hosted API ships. Current
// Pro/Scale/Business prices unchanged tonight; price + quota updates to
// follow with the Stripe Live-key swap.
const PLANS: PlanOption[] = [
  {
    tier_id: "starter",
    label: "Starter",
    price: "$29/mo",
    features:
      "100K verdicts/mo, private switches, email support, 14-day retention. Hosted classifier API quota included on rollout (Q3 2026).",
    upgrade_summary:
      "Starter is the on-ramp for solo developers + indie hackers — 10× the verdict cap of Free, private switches, email support, 14-day retention.",
    status: "coming_soon",
    available_from: "2026 Q3",
  },
  {
    tier_id: "pro",
    label: "Pro",
    price: "$99/mo",
    features:
      "250K verdicts/mo, cohort-tuned BYOK judge orchestration, audit-chain export, unlimited dashboard users, 30-day retention, priority email support. Hosted classifier API quota included on rollout (Q3 2026).",
    upgrade_summary:
      "Pro adds the BYOK judge orchestration layer (cohort-tuned prompts + audit-chain export) on top of Free, with 25× the verdict cap and unlimited dashboard users.",
    status: "available",
  },
  {
    tier_id: "scale",
    label: "Scale",
    price: "$399/mo",
    features:
      "5M verdicts/mo, everything in Pro, plus webhooks, SSO, 90-day retention, priority email. Hosted classifier API quota expanded on rollout (Q3 2026).",
    upgrade_summary:
      "Scale adds webhooks, SSO, and 90-day retention to Pro, with 20× the verdict cap for high-volume ML platforms.",
    status: "available",
  },
  {
    tier_id: "business",
    label: "Business",
    price: "$1,499/mo",
    features:
      "25M verdicts/mo, everything in Scale, plus SOC 2, 99.9% SLA, BAA available, dedicated Slack. Hosted classifier API quota expanded on rollout (Q3 2026); frontier-capacity-management add-on available 2027 Q2.",
    upgrade_summary:
      "Business adds SOC 2, 99.9% SLA, BAA, and dedicated Slack to Scale, with a 5× verdict cap for regulated workloads.",
    status: "available",
  },
  {
    tier_id: "enterprise",
    label: "Enterprise",
    price: "Custom",
    features:
      "Everything in Business, plus private cohort, dedicated inference capacity, custom integrations, named CSM, SOC 2 Type 2 + ISO 27001 + FedRAMP-ready, on-prem deployment option, frontier-capacity-management add-on.",
    upgrade_summary:
      "Enterprise adds private cohort, dedicated capacity, named CSM, and the full compliance stack on top of Business — for teams whose data residency or capacity guarantees require it.",
    status: "sales_led",
  },
];

export default function BillingClient({
  currentTier,
  returnStatus,
}: {
  currentTier: string;
  returnStatus: string | null;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function startCheckout(tier_id: string) {
    setBusy(tier_id);
    setError(null);
    try {
      const r = await fetch("/api/billing/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tier_id }),
      });
      const body = (await r.json()) as { url?: string; error?: string };
      if (!r.ok || !body.url) {
        setError(body.error ?? `Checkout failed (${r.status})`);
        return;
      }
      window.location.href = body.url;
    } finally {
      setBusy(null);
    }
  }

  async function openPortal() {
    setBusy("portal");
    setError(null);
    try {
      const r = await fetch("/api/billing/portal", { method: "POST" });
      const body = (await r.json()) as { url?: string; error?: string; message?: string };
      if (!r.ok || !body.url) {
        setError(body.message ?? body.error ?? `Portal failed (${r.status})`);
        return;
      }
      window.location.href = body.url;
    } finally {
      setBusy(null);
    }
  }

  // Comp ("Partner") tier: no-charge, no Stripe subscription, no upgrade
  // path through the checkout UI. Provisioned via /admin/users/:id/set-tier
  // by operator decision; usage caps and rate limits are scale-equivalent
  // and adjustable by operator. The billing page replaces the plan grid
  // with a static partner message; if the user's plan changes, they get
  // re-tiered via the same admin path (or a Stripe subscription, which
  // would flip the tier back through the webhook).
  if (currentTier === "comp") {
    return (
      <div className="mt-8 space-y-6">
        <section className="surface-card">
          <div className="flex items-baseline justify-between">
            <h2
              style={{
                fontSize: "var(--size-h4)",
                lineHeight: "var(--lh-h4)",
                margin: 0,
              }}
            >
              Partner plan
            </h2>
            <span
              className="badge"
              style={{
                fontSize: "var(--size-caption-small, var(--size-caption))",
                color: "var(--ink-soft)",
                border: "1px solid var(--ink-soft)",
                borderRadius: "999px",
                padding: "2px 10px",
              }}
            >
              Complimentary
            </span>
          </div>
          <p className="prose-brand mt-3">
            You&apos;re on a no-charge partner plan. Usage caps and rate limits are
            set by Postrule and adjusted on request. Reach out to{" "}
            <a href="mailto:hello@postrule.ai">hello@postrule.ai</a> if your needs
            change or you&apos;d like to discuss a paid plan.
          </p>
        </section>
      </div>
    );
  }

  return (
    <div className="mt-8 space-y-6">
      {returnStatus === "success" && (
        <div className="surface-card surface-card--success">
          <p style={{ margin: 0 }}>
            Subscription started. It may take a few seconds for your plan to update —
            refresh the page if your tier still says &quot;free&quot;.
          </p>
        </div>
      )}
      {returnStatus === "canceled" && (
        <div className="surface-card surface-card--muted">
          <p style={{ margin: 0 }}>Checkout canceled.</p>
        </div>
      )}
      {error && (
        <div className="surface-card surface-card--error">
          <p style={{ margin: 0, fontSize: "var(--size-caption)" }}>{error}</p>
        </div>
      )}

      <p
        style={{
          fontSize: "var(--size-caption)",
          color: "var(--ink-soft)",
          margin: 0,
        }}
      >
        Metered on{" "}
        <span style={{ fontWeight: 500, color: "var(--ink)" }}>
          Verdicts / mo
        </span>
        . A verdict is one classification Postrule logs to your account — feeds
        your report card and the cohort.
      </p>

      <section>
        <h2
          style={{
            fontSize: "var(--size-h4)",
            lineHeight: "var(--lh-h4)",
            marginBottom: "var(--space-3)",
          }}
        >
          Available plans
        </h2>
        <div className="grid gap-4 md:grid-cols-3">
          {PLANS.map((p) => {
            const isCurrent = p.tier_id === currentTier;
            return (
              <div
                key={p.tier_id}
                className="surface-card"
                style={{ padding: "var(--space-5)" }}
              >
                <div className="flex items-baseline justify-between">
                  <h3
                    style={{
                      fontSize: "var(--size-h4)",
                      lineHeight: "var(--lh-h4)",
                      margin: 0,
                    }}
                  >
                    {p.label}
                  </h3>
                  <span
                    className="font-mono"
                    style={{ color: "var(--ink)", fontVariantNumeric: "tabular-nums" }}
                  >
                    {p.price}
                  </span>
                </div>
                <p
                  className="mt-2"
                  style={{
                    color: "var(--ink-soft)",
                    fontSize: "var(--size-caption)",
                  }}
                >
                  {p.features}
                </p>
                <p
                  className="mt-3"
                  style={{
                    color: "var(--ink-soft)",
                    fontSize: "var(--size-caption-small, var(--size-caption))",
                  }}
                >
                  <span style={{ fontWeight: 600, color: "var(--ink)" }}>
                    What you get:
                  </span>{" "}
                  {p.upgrade_summary}
                </p>
                {p.status === "coming_soon" ? (
                  <button
                    type="button"
                    disabled
                    className="btn btn-secondary mt-4 w-full"
                    title={`Available ${p.available_from ?? "soon"}`}
                  >
                    {`Available ${p.available_from ?? "soon"}`}
                  </button>
                ) : p.status === "sales_led" ? (
                  <a
                    href="mailto:hello@postrule.ai?subject=Enterprise%20plan%20inquiry"
                    className="btn btn-primary mt-4 w-full"
                    style={{ textAlign: "center", display: "block" }}
                  >
                    Talk to sales
                  </a>
                ) : (
                  <button
                    type="button"
                    onClick={() => startCheckout(p.tier_id)}
                    disabled={busy !== null || isCurrent}
                    className="btn btn-primary mt-4 w-full"
                  >
                    {isCurrent
                      ? "Current plan"
                      : busy === p.tier_id
                        ? "Redirecting…"
                        : "Subscribe"}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      </section>

      <section className="surface-card">
        <h2
          style={{
            fontSize: "var(--size-h4)",
            lineHeight: "var(--lh-h4)",
            marginBottom: "var(--space-2)",
          }}
        >
          Manage subscription
        </h2>
        <p className="prose-brand">
          Update payment method, change plan, view invoices, or cancel via
          Stripe&apos;s hosted portal.
        </p>
        <button
          type="button"
          onClick={openPortal}
          disabled={busy !== null}
          className="btn btn-secondary mt-2"
        >
          {busy === "portal" ? "Opening…" : "Open billing portal"}
        </button>
      </section>
    </div>
  );
}
