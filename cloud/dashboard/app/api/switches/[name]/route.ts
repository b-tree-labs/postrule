// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1
//
// POST   /api/switches/<name>?action=archive   { reason?: string }
// POST   /api/switches/<name>?action=unarchive
//
// Customer-driven archive/unarchive for a switch the signed-in user
// owns. Idempotent on both sides (re-archive → existing row;
// re-unarchive → 200). 404 propagates from the api Worker for cross-
// account / unknown-switch — same data-isolation contract as the
// report-card surface.

import { NextRequest, NextResponse } from "next/server";
import { auth, currentUser } from "@clerk/nextjs/server";
import {
  upsertUser,
  archiveSwitch,
  unarchiveSwitch,
} from "../../../../lib/postrule-api";


async function authedUser() {
  const { userId } = await auth();
  if (!userId) return null;
  const u = await currentUser();
  const email = u?.emailAddresses?.[0]?.emailAddress;
  if (!email) return null;
  return upsertUser(userId, email);
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ name: string }> },
) {
  try {
    const user = await authedUser();
    if (!user) {
      return NextResponse.json({ error: "unauthorized" }, { status: 401 });
    }

    const { name } = await params;
    const action = req.nextUrl.searchParams.get("action");

    if (action === "archive") {
      const body = await req.json().catch(() => ({}));
      const reason =
        typeof body.reason === "string" && body.reason.trim()
          ? body.reason.trim()
          : null;
      const archive = await archiveSwitch(user.user_id, name, reason);
      return NextResponse.json({ archive });
    }

    if (action === "unarchive") {
      await unarchiveSwitch(user.user_id, name);
      return NextResponse.json({ unarchived: true });
    }

    return NextResponse.json(
      { error: "missing_or_invalid_action" },
      { status: 400 },
    );
  } catch (e) {
    const msg = String(e);
    // Surface the api Worker's 404 (cross-account / unknown switch) as
    // a 404 from this route too — keeps the data-isolation shape intact.
    if (msg.includes(" 404 ")) {
      return NextResponse.json({ error: "switch_not_found" }, { status: 404 });
    }
    console.error("POST /api/switches/[name]", e);
    return NextResponse.json({ error: "internal_error" }, { status: 500 });
  }
}
