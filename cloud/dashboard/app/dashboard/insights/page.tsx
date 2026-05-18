// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1

import { auth, currentUser } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";
import Link from "next/link";
import { upsertUser, getInsightsStatus } from "../../../lib/postrule-api";
import InsightsClient from "./insights-client";


export default async function InsightsPage() {
  const { userId } = await auth();
  if (!userId) redirect("/");

  const u = await currentUser();
  const email = u?.emailAddresses?.[0]?.emailAddress;
  if (!email) redirect("/");

  const user = await upsertUser(userId, email);
  const initial = await getInsightsStatus(user.user_id);

  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      <p className="eyebrow eyebrow--accent">Account</p>
      <h1
        className="mt-2"
        style={{ fontSize: "var(--size-h2)", lineHeight: "var(--lh-h2)" }}
      >
        Cohort tuned-defaults
      </h1>
      <p className="prose-brand mt-3">
        Enroll to pull cohort-tuned defaults to your local install and
        contribute anonymous count signals back. Inputs and labels never
        leave your machine.
      </p>

      <InsightsClient initial={initial} />

      <p
        className="mt-6"
        style={{
          fontSize: "var(--size-caption)",
          color: "var(--ink-soft)",
        }}
      >
        <Link href="/privacy">Learn more →</Link>
      </p>
    </main>
  );
}
