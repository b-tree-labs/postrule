// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1
//
// Next.js middleware — runs at the edge for every matched request.
//
// Responsibilities:
//   1. Clerk session injection (clerkMiddleware) — so server components
//      can call auth() / currentUser().
//   2. Security response headers applied to every HTML / JSON response.
//      Headers are applied here (single source of truth) instead of in
//      next.config.mjs because the edge middleware also covers the
//      /api/* route handlers and the Clerk-rewritten paths.
//
// Header rationale (pre-launch audit 2026-05-11, SECURITY_AUDIT-2026-05-11.md):
//   * Content-Security-Policy — restrict what the browser will execute /
//     load. Scoped to allow Clerk + Stripe-hosted-Checkout redirects,
//     Google Fonts, and self. Inline scripts are allowed (Next.js
//     hydration runtime injects them); a nonce-based strategy is a v1.1
//     follow-up to drop 'unsafe-inline' on script-src.
//   * Strict-Transport-Security — force HTTPS for 6 months including
//     subdomains. Once we're confident no http-only origins remain we
//     can request HSTS preload.
//   * X-Frame-Options DENY — defense-in-depth against clickjacking on
//     top of CSP frame-ancestors (which modern browsers honor).
//   * X-Content-Type-Options nosniff — block MIME-type guessing.
//   * Referrer-Policy strict-origin-when-cross-origin — leak the origin
//     only, never paths / query strings, to third-party hosts.
//   * Permissions-Policy — disable browser features we don't use so an
//     XSS or misconfigured embed can't activate them silently.
//
// All five headers are mandatory for the launch (see SECURITY_AUDIT-2026-05-11.md
// finding #5). Add / remove entries here only after re-running the dashboard
// build to confirm no rendered surface depends on the blocked origin.

import { clerkMiddleware } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

// Content-Security-Policy.
//
// Sources of script / style / image / connect / frame that the dashboard
// legitimately uses. Audit before extending:
//   * 'self'              — the dashboard origin (app.postrule.ai + local dev)
//   * clerk.com + clerk.dev + clerk.accounts.dev — Clerk SDK (auth)
//   * *.stripe.com        — Stripe-hosted Checkout / Billing Portal redirects
//   * fonts.googleapis.com + fonts.gstatic.com — next/font/google (Space
//     Grotesk + Geist Mono) — fonts are self-hosted in production by
//     Next.js but the build pipeline still resolves through google.
//   * api.postrule.ai + staging-api.postrule.ai — the api Worker (server-
//     side fetch in route handlers; not strictly needed in CSP, but
//     listed for transparency).
//
// 'unsafe-inline' on script-src is required for Next.js's runtime
// hydration injection. v1.1 will migrate to a per-request nonce strategy
// (next.config.mjs `headers()` callback returning a nonce-injected CSP
// string + Server Components reading the nonce from headers()).
// 'unsafe-inline' on style-src is required for the next/font/google
// inline @font-face declarations and Tailwind's runtime-extracted styles.
//
// frame-ancestors 'none' makes this stronger than X-Frame-Options DENY
// (covers nested iframes too).
// Clerk hosts called out explicitly:
//   *.clerk.com               — Clerk CDN (JS bundle, images)
//   *.clerk.dev               — Clerk auxiliary services
//   *.clerk.accounts.dev      — Clerk-hosted dev/test instances (kept
//                                 in allowlist for rollback-to-pk_test_)
//   clerk.postrule.ai         — Postrule's production Clerk FAPI host
//                                 (custom subdomain provisioned 2026-05-19)
const CSP = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://*.clerk.com https://*.clerk.dev https://*.clerk.accounts.dev https://clerk.postrule.ai https://challenges.cloudflare.com",
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "img-src 'self' data: blob: https://*.clerk.com https://img.clerk.com https://clerk.postrule.ai",
  "font-src 'self' data: https://fonts.gstatic.com",
  "connect-src 'self' https://*.clerk.com https://*.clerk.dev https://*.clerk.accounts.dev https://clerk.postrule.ai https://clerk-telemetry.com https://api.postrule.ai https://staging-api.postrule.ai",
  "frame-src 'self' https://*.clerk.com https://*.clerk.accounts.dev https://clerk.postrule.ai https://challenges.cloudflare.com https://checkout.stripe.com https://billing.stripe.com",
  "form-action 'self' https://checkout.stripe.com https://billing.stripe.com https://*.clerk.com https://*.clerk.accounts.dev https://clerk.postrule.ai",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "object-src 'none'",
  "upgrade-insecure-requests",
].join("; ");

// 6 months. We're confident in HTTPS-everywhere on app.postrule.ai already;
// preload-list submission is a v1.1 follow-up.
const HSTS = "max-age=15552000; includeSubDomains";

const PERMISSIONS_POLICY = [
  "camera=()",
  "microphone=()",
  "geolocation=()",
  "interest-cohort=()",
  "payment=(self)",
  "usb=()",
].join(", ");

function applySecurityHeaders(res: NextResponse): NextResponse {
  res.headers.set("Content-Security-Policy", CSP);
  res.headers.set("Strict-Transport-Security", HSTS);
  res.headers.set("X-Frame-Options", "DENY");
  res.headers.set("X-Content-Type-Options", "nosniff");
  res.headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
  res.headers.set("Permissions-Policy", PERMISSIONS_POLICY);
  return res;
}

export default clerkMiddleware(async (_auth, req) => {
  // Clerk doesn't return a response by default — let the route handler /
  // page render produce it. We wrap the downstream call so we can stamp
  // headers on the final response. NextResponse.next() returns a pass-
  // through that the runtime fills in.
  return applySecurityHeaders(NextResponse.next({ request: req }));
});

export const config = {
  matcher: [
    "/((?!_next|.*\\..*).*)",
    "/(api|trpc)(.*)",
  ],
};
