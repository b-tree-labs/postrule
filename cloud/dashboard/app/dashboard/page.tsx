// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1
//
// Dashboard root — the post-install first-impression surface.
//
// Four stacked sections (top → bottom):
//
//   1. A7 earned-upgrade banner   — conditional, Free + ≥80% usage,
//                                   dismissable per-period
//   2. Tier + usage strip         — current tier, monthly verdict count,
//                                   progress bar, days left in period
//   3. M5 onboarding checklist    — install / login / analyze; collapses
//                                   to "you're set up" once observed-complete
//   4. Recent-activity feed       — last-5 verdicts across all keys
//
// Page is a server component; the banner and checklist render as small
// client components so they can manage dismiss-state and clipboard. The
// initial render is driven entirely by SSR data — no client fetches on
// first paint.

import { auth, currentUser } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";
import Link from "next/link";
import {
  upsertUser,
  getUsage,
  listKeys,
  listRecentVerdicts,
  type UsageInfo,
  type RecentVerdict,
} from "../../lib/postrule-api";
import UpgradeBanner from "./upgrade-banner";
import OnboardingChecklist from "./onboarding-checklist";


// Pretty tier label for the strip. The pricing-tiers.json shape uses
// title-case labels; mirror that here without re-importing the file
// (the dashboard doesn't currently bundle landing/data).
const TIER_LABEL: Record<UsageInfo["tier"], string> = {
  free: "Free",
  pro: "Pro",
  scale: "Scale",
  business: "Business",
};

function daysUntil(iso: string, now: Date): number {
  const ms = new Date(iso).getTime() - now.getTime();
  return Math.max(0, Math.ceil(ms / 86_400_000));
}

function periodKey(iso: string): string {
  // "2026-05-01T00:00:00.000Z" → "2026-05"
  return iso.slice(0, 7);
}

export default async function DashboardPage() {
  const { userId } = await auth();
  if (!userId) {
    redirect("/");
  }

  const clerkUser = await currentUser();
  const email = clerkUser?.emailAddresses?.[0]?.emailAddress;
  if (!email) redirect("/");

  // Single upsert for the row id, then fan out the three reads in
  // parallel. Each is independent at the SQL level so there's no reason
  // to serialize them. allSettled rather than all so a single failed
  // read doesn't blank the entire dashboard (polish-pass 2026-05-11):
  // the user can still see e.g. their checklist if /admin/usage 500s.
  const user = await upsertUser(userId, email);
  const [usageRes, keysRes, recentRes] = await Promise.allSettled([
    getUsage(user.user_id),
    listKeys(user.user_id),
    listRecentVerdicts(user.user_id, 5),
  ]);

  const usage = usageRes.status === "fulfilled" ? usageRes.value : null;
  const keys = keysRes.status === "fulfilled" ? keysRes.value : null;
  const recent = recentRes.status === "fulfilled" ? recentRes.value : null;
  const anyFailed = !usage || !keys || !recent;

  const hasApiKey = keys?.some((k) => !k.revoked_at) ?? false;
  const hasVerdict = (recent?.length ?? 0) > 0;
  const now = new Date();
  const daysLeft = usage ? daysUntil(usage.period_end, now) : 0;

  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      <p className="eyebrow eyebrow--accent">Dashboard</p>
      <h1
        className="mt-2"
        style={{
          fontSize: "var(--size-h2)",
          lineHeight: "var(--lh-h2)",
        }}
      >
        Welcome back
      </h1>
      <p
        className="mt-2"
        style={{
          color: "var(--ink-soft)",
          fontSize: "var(--size-caption)",
        }}
      >
        Signed in as <span className="font-mono">{email}</span>.
      </p>

      <div className="mt-8" style={{ display: "flex", flexDirection: "column", gap: "var(--space-6)" }}>
        {anyFailed && (
          <div
            className="surface-card surface-card--muted"
            role="status"
            style={{ padding: "var(--space-4) var(--space-5)" }}
          >
            <p
              style={{
                margin: 0,
                fontSize: "var(--size-caption)",
                color: "var(--ink-soft)",
              }}
            >
              Some sections could not load just now. The rest of the page is
              live — reload to retry.
            </p>
          </div>
        )}

        {/* 1. Earned-upgrade banner (above the strip, per brief) */}
        {usage && (
          <UpgradeBanner
            tier={usage.tier}
            used={usage.verdicts_this_period}
            cap={usage.cap}
            periodKey={periodKey(usage.period_start)}
          />
        )}

        {/* 2. Tier + usage strip */}
        {usage && <TierUsageStrip usage={usage} daysLeft={daysLeft} />}

        {/* 3. M5 onboarding checklist (collapses when complete) */}
        <OnboardingChecklist hasApiKey={hasApiKey} hasVerdict={hasVerdict} />

        {/* 4. Recent activity feed */}
        {recent !== null && <RecentActivity recent={recent} />}
      </div>
    </main>
  );
}

// ---------------------------------------------------------------------------
// Tier + usage strip. Pure server-render; no interactivity. Progress bar
// width is clamped to 100% so an over-cap row doesn't visually overflow
// the track (the literal count below the bar still reads correctly).
// ---------------------------------------------------------------------------
function TierUsageStrip({ usage, daysLeft }: { usage: UsageInfo; daysLeft: number }) {
  const cap = usage.cap;
  const used = usage.verdicts_this_period;
  const pct = cap != null && cap > 0 ? Math.min(100, (used / cap) * 100) : 0;
  const usedFmt = used.toLocaleString("en-US");
  const capFmt = cap != null ? cap.toLocaleString("en-US") : "Unlimited";
  // 80%+ paints the bar in accent (matches the banner threshold so the
  // two signals visually agree).
  const barColor =
    pct >= 80 ? "var(--accent-deep)" : "var(--ink)";

  return (
    <section className="surface-card" style={{ padding: "var(--space-5)" }}>
      <div
        style={{
          display: "flex",
          gap: "var(--space-4)",
          justifyContent: "space-between",
          alignItems: "baseline",
          flexWrap: "wrap",
        }}
      >
        <div>
          <p className="eyebrow" style={{ margin: 0 }}>
            Current plan
          </p>
          <p
            style={{
              margin: 0,
              fontSize: "var(--size-h3)",
              lineHeight: "var(--lh-h3)",
              color: "var(--ink)",
            }}
          >
            {TIER_LABEL[usage.tier] ?? usage.tier}
          </p>
        </div>
        <div style={{ textAlign: "right" }}>
          <p
            className="font-mono"
            style={{
              margin: 0,
              fontSize: "var(--size-h4)",
              lineHeight: "var(--lh-h4)",
              color: "var(--ink)",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {usedFmt}
            <span style={{ color: "var(--ink-soft)" }}> / {capFmt}</span>
          </p>
          <p
            style={{
              margin: 0,
              fontSize: "var(--size-caption)",
              color: "var(--ink-soft)",
            }}
          >
            verdicts this period
          </p>
        </div>
      </div>

      {/* Progress bar */}
      <div
        role="progressbar"
        aria-valuenow={Math.round(pct)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Verdicts used this period"
        style={{
          marginTop: "var(--space-4)",
          height: "0.5rem",
          background: "var(--ground-soft)",
          borderRadius: "999px",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: `${pct}%`,
            height: "100%",
            background: barColor,
            transition: "width 200ms ease-out",
          }}
        />
      </div>

      <div
        style={{
          marginTop: "var(--space-3)",
          display: "flex",
          gap: "var(--space-4)",
          justifyContent: "space-between",
          fontSize: "var(--size-caption)",
          color: "var(--ink-soft)",
        }}
      >
        <span>
          {daysLeft} {daysLeft === 1 ? "day" : "days"} left in this billing
          period
        </span>
        <Link
          href="/dashboard/billing"
          style={{
            color: "var(--ink-soft)",
            textDecoration: "underline",
            textDecorationColor: "var(--rule)",
            textUnderlineOffset: "3px",
          }}
        >
          Manage plan
        </Link>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Recent activity feed — last-5 verdicts. The /v1/verdicts payload only
// carries the three correctness booleans (no inputs / labels / predictions),
// so each row is "switch_name · phase · paired-correctness · timestamp".
//
// Empty state matches the F11 friction-walk recommendation: it tells the
// user the exact next action, not "no data".
// ---------------------------------------------------------------------------
function RecentActivity({ recent }: { recent: RecentVerdict[] }) {
  if (recent.length === 0) {
    return (
      <section className="surface-card" style={{ padding: "var(--space-5)" }}>
        <p
          className="eyebrow"
          style={{ margin: 0 }}
        >
          Recent activity
        </p>
        <p
          className="mt-3"
          style={{
            color: "var(--ink-soft)",
            fontSize: "var(--size-body)",
            lineHeight: "var(--lh-body)",
            margin: 0,
          }}
        >
          No verdicts yet. Wrap a call site with{" "}
          <code>@ml_switch</code> and run it — verdicts will appear here as
          they&apos;re emitted.
        </p>
      </section>
    );
  }

  // ml_correct is the most useful single-cell summary at runtime: if
  // present, the verdict's correctness is "ml_correct"; otherwise fall
  // back to model_correct, then rule_correct. We render whichever layer
  // produced the decision.
  function summarize(v: RecentVerdict): {
    label: string;
    layer: string;
  } {
    if (v.ml_correct !== null) {
      return { label: v.ml_correct ? "correct" : "incorrect", layer: "ml" };
    }
    if (v.model_correct !== null) {
      return {
        label: v.model_correct ? "correct" : "incorrect",
        layer: "model",
      };
    }
    if (v.rule_correct !== null) {
      return {
        label: v.rule_correct ? "correct" : "incorrect",
        layer: "rule",
      };
    }
    return { label: "—", layer: "—" };
  }

  return (
    <section className="surface-card" style={{ padding: "var(--space-5)" }}>
      <p className="eyebrow" style={{ margin: 0 }}>
        Recent activity
      </p>
      <ul
        style={{
          listStyle: "none",
          padding: 0,
          margin: "var(--space-3) 0 0",
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-2)",
        }}
      >
        {recent.map((v) => {
          const s = summarize(v);
          return (
            <li
              key={v.id}
              style={{
                display: "flex",
                gap: "var(--space-3)",
                alignItems: "baseline",
                fontFamily: "var(--font-mono)",
                fontSize: "var(--size-caption)",
                color: "var(--ink)",
                paddingBottom: "var(--space-2)",
                borderBottom: "1px solid var(--rule)",
                fontVariantNumeric: "tabular-nums",
                flexWrap: "wrap",
              }}
            >
              <span
                style={{
                  fontWeight: 600,
                  minWidth: "10rem",
                }}
              >
                {v.switch_name}
              </span>
              <span style={{ color: "var(--ink-soft)" }}>{v.phase ?? "—"}</span>
              <span
                style={{
                  color:
                    s.label === "correct"
                      ? "var(--accent-deep)"
                      : s.label === "incorrect"
                        ? "#8a1a14"
                        : "var(--ink-soft)",
                }}
              >
                {s.layer} · {s.label}
              </span>
              <span
                style={{
                  marginLeft: "auto",
                  color: "var(--ink-soft)",
                }}
              >
                {formatTimestamp(v.created_at)}
              </span>
            </li>
          );
        })}
      </ul>
      <p
        style={{
          marginTop: "var(--space-3)",
          fontSize: "var(--size-caption)",
          color: "var(--ink-soft)",
          marginBottom: 0,
        }}
      >
        Showing the latest {recent.length} verdict
        {recent.length === 1 ? "" : "s"}. Older verdicts roll up into your
        report cards.
      </p>
    </section>
  );
}

/** ISO → "May 11, 14:33" — short and readable. UTC to avoid timezone
 *  drift on a server-render that may run anywhere. */
function formatTimestamp(iso: string): string {
  // D1 returns "2026-05-11 14:33:21" (space, no TZ); normalize to ISO
  // before parsing so Date() doesn't fall back to NaN.
  const normalized = iso.includes("T") ? iso : iso.replace(" ", "T") + "Z";
  const d = new Date(normalized);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
  });
}
