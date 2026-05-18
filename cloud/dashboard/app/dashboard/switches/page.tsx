// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1
//
// /dashboard/switches — list every switch the authed user has emitted a
// verdict for. Sortable, paginated, with 14-day sparklines per row.

import { auth, currentUser } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";
import { upsertUser, listSwitches } from "../../../lib/postrule-api";
import SwitchesClient from "./switches-client";


export default async function SwitchesListPage({
  searchParams,
}: {
  searchParams: Promise<{ include_archived?: string }>;
}) {
  const { userId } = await auth();
  if (!userId) redirect("/");

  const u = await currentUser();
  const email = u?.emailAddresses?.[0]?.emailAddress;
  if (!email) redirect("/");

  const sp = await searchParams;
  const includeArchived = sp.include_archived === "true";

  const user = await upsertUser(userId, email);
  const data = await listSwitches(user.user_id, includeArchived);

  return (
    <main className="mx-auto max-w-5xl px-6 py-12">
      <p className="eyebrow eyebrow--accent">Account</p>
      <h1
        className="mt-2"
        style={{
          fontSize: "var(--size-h2)",
          lineHeight: "var(--lh-h2)",
        }}
      >
        Switches
      </h1>
      <p className="prose-brand mt-3">
        Every site you&apos;ve wrapped with <code>@ml_switch</code> appears
        below once it records a verdict. Click a switch to open its report
        card — phase timeline, paired-correctness gate, drift signals,
        cost trajectory.
      </p>
      <SwitchesClient
        switches={data.switches}
        sparklineWindowDays={data.sparkline_window_days}
        archivedCount={data.archived_count}
        includeArchived={includeArchived}
        tier={user.tier}
      />
    </main>
  );
}
