// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1
//
// DELETE /api/keys/:id → revoke (soft-delete) one of the signed-in
// user's API keys. Cross-user revoke is blocked at both layers:
// the api Worker checks user_id matches, and we only ever pass our
// own Clerk-authenticated user_id here.

import { NextRequest, NextResponse } from "next/server";
import { auth, currentUser } from "@clerk/nextjs/server";
import { upsertUser, revokeKey } from "../../../../lib/postrule-api";


export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const { userId } = await auth();
    if (!userId) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
    const u = await currentUser();
    const email = u?.emailAddresses?.[0]?.emailAddress;
    if (!email) return NextResponse.json({ error: "no_email_on_clerk_user" }, { status: 400 });

    const { id: idParam } = await params;
    const keyId = Number(idParam);
    if (!Number.isInteger(keyId) || keyId <= 0) {
      return NextResponse.json({ error: "invalid_id" }, { status: 400 });
    }

    const user = await upsertUser(userId, email);
    await revokeKey(user.user_id, keyId);
    return NextResponse.json({ revoked: true, id: keyId });
  } catch (e) {
    console.error("DELETE /api/keys/[id]", e);
    return NextResponse.json({ error: "internal_error" }, { status: 500 });
  }
}
