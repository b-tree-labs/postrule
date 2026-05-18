// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1
//
// /api/cli-auth — dashboard side of the OAuth 2.0 Device Authorization
// Grant (RFC 8628) used by `postrule login`.
//
//   GET  /api/cli-auth?user_code=ABCD-2345
//        Look up a pending CLI session. Returns metadata the page
//        renders for the user to confirm. Clerk-auth required so an
//        anonymous visitor can't probe codes.
//
//   POST /api/cli-auth
//        body: { user_code: string, action: 'authorize' | 'deny' }
//        Authorize / deny the session for the currently signed-in
//        Clerk user. The api Worker mints the API key when the CLI
//        next polls /v1/device/token; this route never sees the key.

import { NextRequest, NextResponse } from "next/server";
import { auth, currentUser } from "@clerk/nextjs/server";
import {
  upsertUser,
  lookupCliSession,
  authorizeCliSession,
  denyCliSession,
} from "../../../lib/postrule-api";


const USER_CODE_RE = /^[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$/;

/**
 * Normalize a user-typed code: uppercase, strip whitespace and the
 * separator if missing/extra. The dashboard form sometimes receives
 * `abcd2345` or `ABCD 2345`; canonicalize before validating.
 */
function normalizeUserCode(raw: string): string {
  const stripped = raw.trim().toUpperCase().replace(/\s+/g, '').replace(/-/g, '');
  if (stripped.length !== 8) return raw;
  return `${stripped.slice(0, 4)}-${stripped.slice(4)}`;
}

async function requireAuth() {
  const { userId } = await auth();
  if (!userId) {
    return { error: NextResponse.json({ error: "no_session" }, { status: 401 }) };
  }
  const u = await currentUser();
  const email = u?.emailAddresses?.[0]?.emailAddress;
  if (!email) {
    return {
      error: NextResponse.json({ error: "no_email_on_clerk_user" }, { status: 400 }),
    };
  }
  return { userId, email };
}

// ---------------------------------------------------------------------------
// GET — pre-authorize lookup. The page calls this once the user has
// pasted a code, before showing the Authorize / Deny buttons.
// ---------------------------------------------------------------------------
export async function GET(req: NextRequest) {
  const a = await requireAuth();
  if ("error" in a) return a.error;

  const raw = req.nextUrl.searchParams.get("user_code");
  if (!raw) {
    return NextResponse.json({ error: "missing_user_code" }, { status: 400 });
  }
  const userCode = normalizeUserCode(raw);
  if (!USER_CODE_RE.test(userCode)) {
    return NextResponse.json({ error: "invalid_user_code" }, { status: 400 });
  }

  try {
    const session = await lookupCliSession(userCode);
    return NextResponse.json({ user_code: userCode, ...session });
  } catch (e) {
    const msg = String(e);
    if (msg.includes(" 404 ")) {
      return NextResponse.json({ error: "not_found" }, { status: 404 });
    }
    console.error("cli-auth GET failed", e);
    return NextResponse.json({ error: "lookup_failed" }, { status: 500 });
  }
}

// ---------------------------------------------------------------------------
// POST — authorize or deny.
// ---------------------------------------------------------------------------
export async function POST(req: NextRequest) {
  const a = await requireAuth();
  if ("error" in a) return a.error;

  let body: { user_code?: unknown; action?: unknown } = {};
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }

  if (typeof body.user_code !== "string") {
    return NextResponse.json({ error: "missing_user_code" }, { status: 400 });
  }
  const userCode = normalizeUserCode(body.user_code);
  if (!USER_CODE_RE.test(userCode)) {
    return NextResponse.json({ error: "invalid_user_code" }, { status: 400 });
  }
  if (body.action !== "authorize" && body.action !== "deny") {
    return NextResponse.json({ error: "invalid_action" }, { status: 400 });
  }

  try {
    if (body.action === "deny") {
      await denyCliSession(userCode);
      return NextResponse.json({ ok: true, action: "denied" });
    }

    // Authorize path: ensure the current Clerk user has a row in the
    // postrule users table (idempotent), then call admin/authorize.
    const postruleUser = await upsertUser(a.userId, a.email);
    await authorizeCliSession(userCode, postruleUser.user_id);
    return NextResponse.json({ ok: true, action: "authorized" });
  } catch (e) {
    const msg = String(e);
    if (msg.includes(" 404 ")) {
      return NextResponse.json({ error: "not_found" }, { status: 404 });
    }
    if (msg.includes(" 410 ")) {
      return NextResponse.json({ error: "expired" }, { status: 410 });
    }
    if (msg.includes(" 409 ")) {
      return NextResponse.json({ error: "session_state_conflict" }, { status: 409 });
    }
    console.error("cli-auth POST failed", e);
    return NextResponse.json({ error: "operation_failed" }, { status: 500 });
  }
}
