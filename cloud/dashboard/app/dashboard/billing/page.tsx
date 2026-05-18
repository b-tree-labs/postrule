// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1

import { auth, currentUser } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";
import { upsertUser } from "../../../lib/postrule-api";
import BillingClient from "./billing-client";


export default async function BillingPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string; session_id?: string }>;
}) {
  const { userId } = await auth();
  if (!userId) redirect("/");

  const u = await currentUser();
  const email = u?.emailAddresses?.[0]?.emailAddress;
  if (!email) redirect("/");

  const user = await upsertUser(userId, email);
  const sp = await searchParams;

  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      <p className="eyebrow eyebrow--accent">Account</p>
      <h1
        className="mt-2"
        style={{
          fontSize: "var(--size-h2)",
          lineHeight: "var(--lh-h2)",
        }}
      >
        Billing
      </h1>
      <p className="prose-brand mt-3">
        Current plan:{" "}
        <span className="font-mono" style={{ textTransform: "capitalize" }}>
          {user.tier}
        </span>
        .
      </p>
      <BillingClient currentTier={user.tier} returnStatus={sp.status ?? null} />
    </main>
  );
}
