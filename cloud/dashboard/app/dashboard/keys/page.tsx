// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1

import { auth, currentUser } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";
import { upsertUser, listKeys } from "../../../lib/postrule-api";
import KeysClient from "./keys-client";


export default async function KeysPage() {
  const { userId } = await auth();
  if (!userId) redirect("/");

  const u = await currentUser();
  const email = u?.emailAddresses?.[0]?.emailAddress;
  if (!email) redirect("/");

  const user = await upsertUser(userId, email);
  const initialKeys = await listKeys(user.user_id);

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
        API keys
      </h1>
      <p className="prose-brand mt-3">
        Plan: <span className="font-mono">{user.tier}</span>. The plaintext
        key is shown only once at creation — store it somewhere safe
        (e.g. <code>~/.postrule/credentials</code>) or skip the copy-paste
        entirely by running <code>postrule login</code> from your terminal.
      </p>
      <KeysClient initialKeys={initialKeys} />
    </main>
  );
}
