// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1
//
// GET  /api/keys  → list the signed-in user's API keys (metadata only)
// POST /api/keys  → issue a new key; plaintext returned ONCE in the body

import { NextRequest, NextResponse } from "next/server";
import { auth, currentUser } from "@clerk/nextjs/server";
import { upsertUser, listKeys, issueKey } from "../../../lib/postrule-api";


async function authedUser() {
  const { userId } = await auth();
  if (!userId) return null;
  const u = await currentUser();
  const email = u?.emailAddresses?.[0]?.emailAddress;
  if (!email) return null;
  // Idempotent: ensures a row exists in postrule-events.users for this Clerk user.
  return upsertUser(userId, email);
}

export async function GET() {
  try {
    const user = await authedUser();
    if (!user) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
    const keys = await listKeys(user.user_id);
    return NextResponse.json({ keys });
  } catch (e) {
    console.error("GET /api/keys", e);
    return NextResponse.json({ error: "internal_error" }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  try {
    const user = await authedUser();
    if (!user) return NextResponse.json({ error: "unauthorized" }, { status: 401 });

    const body = await req.json().catch(() => ({}));
    const name = typeof body.name === "string" && body.name.trim() ? body.name.trim() : null;
    const environment = body.environment === "test" ? "test" : "live";

    const issued = await issueKey(user.user_id, name, environment);
    return NextResponse.json(issued);
  } catch (e) {
    console.error("POST /api/keys", e);
    return NextResponse.json({ error: "internal_error" }, { status: 500 });
  }
}
