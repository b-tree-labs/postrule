// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1

import { auth, currentUser } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";
import { upsertUser, getPreferences } from "../../../lib/postrule-api";
import SettingsClient from "./settings-client";


export default async function SettingsPage() {
  const { userId } = await auth();
  if (!userId) redirect("/");

  const u = await currentUser();
  const email = u?.emailAddresses?.[0]?.emailAddress;
  if (!email) redirect("/");

  const user = await upsertUser(userId, email);
  const prefs = await getPreferences(user.user_id);

  // If the user hasn't set a custom display_name, fall back to whatever
  // Clerk has — the Settings page treats Clerk's name as the default
  // placeholder so the field shows something sensible on first load.
  const clerkDisplayName =
    [u?.firstName, u?.lastName].filter(Boolean).join(" ").trim() || null;

  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      <p className="eyebrow eyebrow--accent">Account</p>
      <h1
        className="mt-2"
        style={{ fontSize: "var(--size-h2)", lineHeight: "var(--lh-h2)" }}
      >
        Settings
      </h1>
      <p className="prose-brand mt-3">
        Account preferences for{" "}
        <span className="font-mono">{prefs.email}</span>.
      </p>
      <SettingsClient initial={prefs} clerkDisplayName={clerkDisplayName} />
    </main>
  );
}
