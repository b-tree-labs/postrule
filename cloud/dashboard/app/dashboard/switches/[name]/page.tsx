// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1
//
// /dashboard/switches/<name> — per-switch report card.
//
// Server component: fetches the structured report JSON via the
// service-token admin proxy AND the canonical Markdown report card from
// the same surface (we ask for the JSON payload on the dashboard for
// rich rendering; the canonical Markdown excerpt is generated client-
// side from those same numbers — same shape as docs/sample-reports/
// triage_rule.md).
//
// 404 propagates from the data lib when the user doesn't own the switch,
// matching the data-isolation contract on the API side.

import { auth, currentUser } from "@clerk/nextjs/server";
import { notFound, redirect } from "next/navigation";
import { upsertUser, getSwitchReport } from "../../../../lib/postrule-api";
import SwitchReportClient from "./switch-report-client";


export default async function SwitchReportPage({
  params,
}: {
  params: Promise<{ name: string }>;
}) {
  const { userId } = await auth();
  if (!userId) redirect("/");

  const u = await currentUser();
  const email = u?.emailAddresses?.[0]?.emailAddress;
  if (!email) redirect("/");

  const { name } = await params;
  // Next.js dynamic-segment value is already decoded.
  const switchName = name;

  const user = await upsertUser(userId, email);
  const report = await getSwitchReport(user.user_id, switchName, 30);
  if (!report) {
    notFound();
  }

  return (
    <main className="mx-auto max-w-4xl px-6 py-12">
      <SwitchReportClient
        switchName={switchName}
        report={report}
        tier={user.tier}
      />
    </main>
  );
}
