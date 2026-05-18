// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1

import { auth, currentUser } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";
import CliAuthClient from "./cli-auth-client";


export default async function CliAuthPage({
  searchParams,
}: {
  searchParams: Promise<{ user_code?: string }>;
}) {
  const { userId } = await auth();
  if (!userId) {
    // The middleware should already have caught this, but guard anyway:
    // bounce to sign-in with a return-to that brings the user back here.
    redirect("/sign-in?redirect_url=/cli-auth");
  }

  const u = await currentUser();
  const email = u?.emailAddresses?.[0]?.emailAddress ?? "unknown";
  const sp = await searchParams;
  const initialCode = sp.user_code ?? "";

  return (
    <main className="mx-auto max-w-xl px-6 py-12">
      <p className="eyebrow eyebrow--accent">CLI</p>
      <h1
        className="mt-2"
        style={{
          fontSize: "var(--size-h2)",
          lineHeight: "var(--lh-h2)",
        }}
      >
        Authorize Postrule CLI
      </h1>
      <p className="prose-brand mt-3">
        Signed in as <span className="font-mono">{email}</span>. Confirm the
        device + code shown in your terminal, then click Authorize. The CLI
        will receive a fresh API key automatically.
      </p>

      <CliAuthClient initialCode={initialCode} />

      <hr
        className="mt-12"
        style={{ border: 0, borderTop: "var(--border)" }}
      />
      <p
        className="mt-4"
        style={{
          fontSize: "var(--size-micro)",
          color: "var(--ink-soft)",
          lineHeight: "var(--lh-micro)",
        }}
      >
        This page implements the dashboard side of an OAuth 2.0 Device
        Authorization Grant (RFC 8628). The plaintext API key is generated
        by the api Worker only after you authorize, and is delivered to the
        CLI directly — the dashboard never sees it.
      </p>
    </main>
  );
}
