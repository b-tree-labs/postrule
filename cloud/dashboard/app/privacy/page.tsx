// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1
//
// Privacy policy page. Linked from the dashboard footer + Stripe Checkout
// footer (Stripe requires legal links to be reachable from the checkout
// flow when collecting card details).
//
// This is the v1.0 launch policy. It describes the telemetry contract
// promised at sign-in (Q4, 2026-05-11): unauthenticated → zero;
// signed-in → count-only verdict events by default; opt-out via env
// var, per-switch config, or dashboard setting; richer data opt-in
// only. Drafted for B-Tree Labs based on what we actually do today;
// it is NOT legal advice. Update the "Last updated" stamp on every
// revision and re-read it like a CFO would before publishing.

import Link from "next/link";


export const metadata = {
  title: "Privacy Policy — Postrule",
  description: "How B-Tree Labs handles your data when you use Postrule.",
};

export default function PrivacyPage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-16">
      <p className="eyebrow eyebrow--accent">Legal</p>
      <h1
        className="mt-2"
        style={{
          fontSize: "var(--size-h1)",
          lineHeight: "var(--lh-h1)",
        }}
      >
        Privacy Policy
      </h1>
      <p
        className="mt-1"
        style={{
          fontSize: "var(--size-caption)",
          color: "var(--ink-soft)",
        }}
      >
        Last updated: 2026-05-11
      </p>

      <div className="prose-brand mt-8">
        <div className="callout">
          <p>
            <strong>The short version.</strong> If you haven&apos;t signed
            in, Postrule sends us nothing. If you have signed in, we collect
            count-only records of your classification results — never the
            text you classified, never the labels you produced. You can
            turn that off at any time.
          </p>
        </div>

        <h2>Who we are</h2>
        <p>
          Postrule is a product of <strong>B-Tree Labs</strong>, a DBA of
          B-Tree Ventures, LLC, registered in Wyoming, USA. Throughout this
          policy, &ldquo;we&rdquo; / &ldquo;our&rdquo; refers to B-Tree
          Labs.
        </p>

        <h2>What we collect from anyone who signs up</h2>
        <ul>
          <li>
            <strong>Account email.</strong> Collected at sign-in through
            our identity provider (Clerk). Used to identify your account
            and send receipts and security alerts. Nothing else.
          </li>
          <li>
            <strong>Payment details (paid plans only).</strong> Handled
            entirely by Stripe. We never see or store card numbers; Stripe
            returns a customer ID we associate with your account.
          </li>
          <li>
            <strong>Operational logs.</strong> Request paths, response
            codes, and error traces, kept for 7 days for debugging. IP
            addresses are redacted after 24 hours.
          </li>
        </ul>

        <h2>The telemetry contract</h2>
        <p>
          Postrule is a Python library you install on your own machine.
          Whether it sends us anything depends on a single fact: are you
          signed in?
        </p>

        <h3>Unauthenticated use — nothing crosses the wire</h3>
        <p>
          When you run Postrule without signing in (via{" "}
          <code>pip install postrule</code> and no API key), it works
          entirely on your computer. We don&apos;t see your code, your
          inputs, your labels, or even the fact that you ran it. No
          telemetry, no pings, no analytics. The open-source library has
          no network calls in this mode.
        </p>

        <h3>Signed-in use — count-only verdict events, by default</h3>
        <p>
          When you sign in (CLI <code>postrule login</code> or via the
          dashboard) we tell you, at sign-in, that Postrule will start
          sending us small &ldquo;verdict events&rdquo; about how your
          classifiers are performing. A verdict event contains:
        </p>
        <ul>
          <li>The name of the switch (e.g. <code>ticket_priority</code>) — chosen by you.</li>
          <li>Its lifecycle phase (e.g. <code>RULE</code>, <code>LLM</code>, <code>ML</code>).</li>
          <li>Whether the result matched the safety floor (a true/false flag).</li>
          <li>A timestamp.</li>
          <li>
            A one-way hash of your account credential (HMAC-SHA256, not
            reversible — so we can group events by account without storing
            your key).
          </li>
        </ul>
        <p>That&apos;s the full list. Specifically:</p>
        <ul>
          <li>
            <strong>We never see the input text</strong> you classified.
            It stays on your machine.
          </li>
          <li>
            <strong>We never see the label</strong> your classifier
            produced.
          </li>
          <li>
            <strong>We never see any metadata</strong> about your
            inputs — no length, no language, no fingerprint, no excerpt.
          </li>
        </ul>
        <p>
          We use these counts to keep the service running (tier usage,
          rate limiting, infrastructure capacity) and to power the
          cohort-tuned-defaults feature, which combines anonymous verdict
          counts across accounts to suggest better starting points for
          new switches.
        </p>

        <h3>How to turn it off — three ways, all honored</h3>
        <p>
          If you&apos;d rather not send any verdict events even though
          you&apos;re signed in, any one of these will silence them:
        </p>
        <ul>
          <li>
            Set the environment variable{" "}
            <code>POSTRULE_NO_TELEMETRY=1</code> wherever the SDK runs. This
            is the bluntest off-switch and overrides everything else.
          </li>
          <li>
            Disable it per switch in your code:{" "}
            <code>@switch(&hellip;, telemetry=False)</code>.
          </li>
          <li>
            Toggle it off for your whole account on the dashboard, at{" "}
            <code>/dashboard/settings</code>. Your setting applies to
            every machine that uses your account.
          </li>
        </ul>
        <p>
          All three are checked on every call; whichever is most
          restrictive wins. If you opt out, the SDK skips the network
          send entirely — there is no &ldquo;we still record but ignore&rdquo;
          step.
        </p>

        <h3>Richer data — only if you turn it on, per switch</h3>
        <p>
          Some features (for example, enrolling a switch in the
          cohort-tuned-defaults program so it can benefit from
          peer-account data) require slightly richer telemetry — for
          instance, the categorical output your classifier produced
          (still never the input). Those programs are <strong>off by
          default</strong> and must be enabled per switch in your code or
          on the dashboard. We list the exact additional fields on the
          enrollment page before you opt in.
        </p>

        <h2>What we do not collect</h2>
        <ul>
          <li>
            The text, payloads, or labels you classify (under any
            telemetry mode).
          </li>
          <li>Tracking cookies, advertising cookies, or fingerprints.</li>
          <li>
            Third-party analytics (no Google Analytics, no Segment, no
            tracking pixels).
          </li>
        </ul>
        <p>
          We use a single session cookie (set by Clerk) and a Stripe
          checkout cookie during paid-tier upgrade flows. That&apos;s it.
        </p>

        <h2>Where data lives</h2>
        <p>
          Account and billing state live in Cloudflare D1 (SQLite,
          replicated within Cloudflare&apos;s WNAM region). Verdict-event
          counts live in Cloudflare KV and D1. Operational logs live in
          Cloudflare Workers Observability. Card details live with
          Stripe. We do not transfer your data outside these providers.
        </p>

        <h2>How long we keep things</h2>
        <ul>
          <li>
            <strong>Account data</strong> — until you delete your account.
          </li>
          <li>
            <strong>Verdict events</strong> — retained per your plan:
            Free 7 days, Pro 30 days, Scale 90 days, Business 1 year.
            After that they are deleted from active storage.
          </li>
          <li>
            <strong>Operational logs</strong> — 7 days. IPs redacted at 24
            hours.
          </li>
          <li>
            <strong>Billing records</strong> — 7 years (US federal tax-record
            retention).
          </li>
        </ul>

        <h2>Your rights</h2>
        <p>
          You can request a copy of your data, or delete your account and
          every record we have, by emailing{" "}
          <a href="mailto:privacy@postrule.ai">privacy@postrule.ai</a>. We
          reply within 30 days. Residents of the EU/UK have GDPR rights
          of access, rectification, erasure, restriction, portability,
          and objection. California residents have CCPA rights of
          access, deletion, and opt-out of sale (we don&apos;t sell your
          data and never will).
        </p>

        <h2>Changes to this policy</h2>
        <p>
          We&apos;ll post material changes here and bump the &ldquo;Last
          updated&rdquo; date. If you have an active account when a
          change is made, we&apos;ll email you within 7 days of
          publishing it. If the change expands what we collect, we ask
          you to re-consent before it applies to your account.
        </p>

        <h2>Contact</h2>
        <p>
          Questions:{" "}
          <a href="mailto:privacy@postrule.ai">privacy@postrule.ai</a>.
          Mailing address available on request.
        </p>

        <p
          style={{
            marginTop: "var(--space-8)",
            fontSize: "var(--size-caption)",
            color: "var(--ink-soft)",
          }}
        >
          See also our <Link href="/terms">Terms of Service</Link>.
        </p>
      </div>
    </main>
  );
}
