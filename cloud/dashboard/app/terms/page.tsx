// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1
//
// Terms of Service. Linked from the dashboard footer + Stripe Checkout
// footer. v1.0 launch boilerplate; revisit with counsel before any
// material expansion of paid surface.

import Link from "next/link";


export const metadata = {
  title: "Terms of Service — Postrule",
  description: "Terms governing your use of Postrule.",
};

export default function TermsPage() {
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
        Terms of Service
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
        <h2>1. The service</h2>
        <p>
          Postrule is a graduated-autonomy classification primitive provided
          by <strong>B-Tree Labs</strong> (a DBA of B-Tree Ventures, LLC,
          Wyoming, USA). It comprises:
        </p>
        <ul>
          <li>
            An open-source Python library (<code>pip install postrule</code>),
            licensed Apache 2.0 for the client SDK and BSL 1.1 for the
            analyzer/server components. See{" "}
            <a href="https://github.com/b-tree-labs/postrule/blob/main/LICENSE.md">
              LICENSE.md
            </a>{" "}
            for the canonical split.
          </li>
          <li>
            A hosted API at <code>api.postrule.ai</code> with tier-gated
            usage caps and a paid upgrade path.
          </li>
          <li>
            This dashboard at <code>app.postrule.ai</code> for account,
            billing, and key management.
          </li>
        </ul>

        <h2>2. Accounts</h2>
        <p>
          You must be at least 13 years old (16 in the EU). You agree to
          provide accurate information and keep your credentials secure;
          you are responsible for activity under your account. We may
          suspend accounts that abuse the service, including: scraping,
          credential sharing, exceeding tier limits via multiple accounts,
          or violating applicable law.
        </p>

        <h2>3. Acceptable use</h2>
        <p>
          You may use Postrule to classify any input you have the lawful
          right to process. You may not use the service to:
        </p>
        <ul>
          <li>
            Generate or distribute illegal content, including CSAM,
            non-consensual intimate imagery, or content that infringes
            third-party intellectual property.
          </li>
          <li>
            Build classifiers that target individuals on the basis of
            protected attributes (race, religion, sexual orientation,
            etc.) for surveillance, discrimination, or harm.
          </li>
          <li>
            Reverse-engineer rate limits or attempt to bypass the tier
            caps documented in your subscription.
          </li>
          <li>
            Operate a competing hosted service using the BSL-licensed
            analyzer/server components — see the License for the full
            Additional Use Grant text.
          </li>
        </ul>

        <h2>4. Billing</h2>
        <p>
          Paid plans are billed monthly via Stripe. Charges renew until
          you cancel; cancellation takes effect at the end of the current
          billing period (no proration, no refunds for partial months).
          Free tier requires no payment method and has no minimum
          commitment.
        </p>
        <p>
          We may change pricing with 30 days&apos; notice; existing paid
          subscriptions remain on their current price until the next
          renewal after the notice period.
        </p>

        <h2>5. Service availability</h2>
        <p>
          We aim for 99% monthly uptime on the hosted API but do not
          offer a contractual SLA at this stage. Status:{" "}
          <a href="https://status.postrule.ai">status.postrule.ai</a>.
        </p>

        <h2>6. Your data and telemetry</h2>
        <p>
          You retain all rights in the inputs you submit and the outputs
          Postrule produces. We process your data only to provide the
          service. The full data and telemetry contract — including the
          default-on, count-only verdict events sent when you&apos;re
          signed in, and the three ways to turn them off — is described
          in our <Link href="/privacy">Privacy Policy</Link>. We never
          see the text or labels you classify, and we do not train models
          on your data.
        </p>

        <h2>7. Disclaimers</h2>
        <p>
          The service is provided &ldquo;as is.&rdquo; To the maximum
          extent permitted by law, we disclaim warranties of
          merchantability, fitness for a particular purpose, and
          non-infringement.{" "}
          <strong>
            Postrule is a classification primitive, not a substitute for
            human review in safety-critical contexts.
          </strong>{" "}
          Outcomes you derive from using Postrule are your responsibility.
        </p>

        <h2>8. Limitation of liability</h2>
        <p>
          To the maximum extent permitted by law, B-Tree Labs&apos;
          aggregate liability arising out of or relating to the service
          will not exceed the greater of (a) USD 100, or (b) the amounts
          you paid us in the 12 months preceding the claim. We are not
          liable for indirect, incidental, consequential, special, or
          punitive damages.
        </p>

        <h2>9. Termination</h2>
        <p>
          You may close your account at any time from the dashboard. We
          may terminate accounts for violations of these Terms with
          notice; for severe violations (e.g., illegal content), we may
          terminate immediately.
        </p>

        <h2>10. Governing law</h2>
        <p>
          These Terms are governed by the laws of the State of Wyoming,
          USA, without regard to conflict-of-law principles. Disputes
          will be resolved in the state or federal courts of Laramie
          County, Wyoming.
        </p>

        <h2>11. Changes</h2>
        <p>
          We may update these Terms; material changes will be posted
          here and (for active accounts) emailed at least 7 days before
          they take effect. Continued use after the effective date
          constitutes acceptance.
        </p>

        <h2>Contact</h2>
        <p>
          Questions: <a href="mailto:legal@postrule.ai">legal@postrule.ai</a>.
        </p>
      </div>
    </main>
  );
}
