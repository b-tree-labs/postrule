// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1
//
// /api/settings — Clerk-authenticated route handler backing the
// /dashboard/settings page. Proxies to the api Worker's /admin/whoami
// endpoint so the service token stays on the server.
//
//   GET   /api/settings  → current preferences for the signed-in user
//   PATCH /api/settings  → update display_name and/or telemetry_enabled
//
// PATCH body shape: { display_name?: string | null, telemetry_enabled?: boolean }.
// Absent fields are left untouched; null display_name clears the override.

import { NextRequest, NextResponse } from "next/server";
import { auth, currentUser } from "@clerk/nextjs/server";
import {
  upsertUser,
  getPreferences,
  patchPreferences,
  type PreferencesPatch,
} from "../../../lib/postrule-api";


async function authedUser() {
  const { userId } = await auth();
  if (!userId) return null;
  const u = await currentUser();
  const email = u?.emailAddresses?.[0]?.emailAddress;
  if (!email) return null;
  return upsertUser(userId, email);
}

export async function GET() {
  try {
    const user = await authedUser();
    if (!user) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
    const prefs = await getPreferences(user.user_id);
    return NextResponse.json(prefs);
  } catch (e) {
    console.error("GET /api/settings", e);
    return NextResponse.json({ error: "internal_error" }, { status: 500 });
  }
}

export async function PATCH(req: NextRequest) {
  try {
    const user = await authedUser();
    if (!user) return NextResponse.json({ error: "unauthorized" }, { status: 401 });

    const body = (await req.json().catch(() => ({}))) as Partial<PreferencesPatch> & {
      display_name?: unknown;
      telemetry_enabled?: unknown;
    };

    // Mirror the api Worker's validation so we 400 at the dashboard edge
    // instead of bouncing through the admin call. The Worker re-validates
    // (defense in depth).
    const patch: PreferencesPatch = {};
    if (Object.prototype.hasOwnProperty.call(body, "display_name")) {
      if (typeof body.display_name === "string") {
        patch.display_name = body.display_name;
      } else if (body.display_name === null) {
        patch.display_name = null;
      } else {
        return NextResponse.json({ error: "invalid_display_name" }, { status: 400 });
      }
    }
    if (Object.prototype.hasOwnProperty.call(body, "telemetry_enabled")) {
      if (typeof body.telemetry_enabled !== "boolean") {
        return NextResponse.json(
          { error: "invalid_telemetry_enabled" },
          { status: 400 },
        );
      }
      patch.telemetry_enabled = body.telemetry_enabled;
    }
    if (Object.keys(patch).length === 0) {
      return NextResponse.json({ error: "no_fields_to_update" }, { status: 400 });
    }

    const updated = await patchPreferences(user.user_id, patch);
    return NextResponse.json(updated);
  } catch (e) {
    console.error("PATCH /api/settings", e);
    return NextResponse.json({ error: "internal_error" }, { status: 500 });
  }
}
