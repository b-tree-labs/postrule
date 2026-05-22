// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1

import Link from "next/link";
import {
  SignedIn,
  SignedOut,
  SignInButton,
  SignUpButton,
} from "@clerk/nextjs";


export default function Home() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-16">
      <p className="eyebrow eyebrow--accent">Postrule Cloud</p>
      <h1
        className="mt-3"
        style={{
          fontSize: "var(--size-h1)",
          lineHeight: "var(--lh-h1)",
          letterSpacing: "-0.01em",
        }}
      >
        Self-taught classifiers.
      </h1>

      <div className="prose-brand mt-6">
        <p>
          A free account saves your analyses, shares them with teammates,
          and lets your switches pull cohort-tuned defaults. The
          open-source primitive runs without an account if you&apos;d
          rather try it locally first.
        </p>
      </div>

      <div className="mt-8 flex flex-wrap items-center gap-3">
        <SignedOut>
          <SignUpButton mode="modal">
            <button type="button" className="btn btn-primary">
              Create free account
            </button>
          </SignUpButton>
          <SignInButton mode="modal">
            <button type="button" className="btn btn-secondary">
              Sign in
            </button>
          </SignInButton>
        </SignedOut>
        <SignedIn>
          <Link href="/dashboard" className="btn btn-primary">
            Open dashboard
          </Link>
        </SignedIn>
      </div>

      <p
        className="mt-6"
        style={{
          fontSize: "var(--size-caption)",
          color: "var(--ink-soft)",
        }}
      >
        Sign-up uses GitHub OAuth or a magic-link email. No password. We
        send count-only telemetry by default — see{" "}
        <Link
          href="/privacy"
          style={{ color: "var(--ink-soft)", textDecorationColor: "var(--rule)" }}
        >
          Privacy
        </Link>{" "}
        for the full contract and how to opt out.
      </p>
    </main>
  );
}
