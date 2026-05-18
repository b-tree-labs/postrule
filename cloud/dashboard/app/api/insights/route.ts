// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1
//
// /api/insights — Clerk-authenticated route handler that proxies the
// dashboard /dashboard/insights toggle to the api Worker's /admin/insights/*
// surface. The handler is the only legitimate caller of the admin endpoints
// from the client side; the service token stays on the server.
//
//   GET    /api/insights         → current enrollment status + cohort size
//   POST   /api/insights         → enroll the signed-in user
//   DELETE /api/insights         → leave the cohort

import { NextResponse } from "next/server";
import { auth, currentUser } from "@clerk/nextjs/server";
import {
  upsertUser,
  getInsightsStatus,
  enrollInsights,
  leaveInsights,
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
    const status = await getInsightsStatus(user.user_id);
    return NextResponse.json(status);
  } catch (e) {
    console.error("GET /api/insights", e);
    return NextResponse.json({ error: "internal_error" }, { status: 500 });
  }
}

export async function POST() {
  try {
    const user = await authedUser();
    if (!user) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
    await enrollInsights(user.user_id);
    // Re-fetch full status so the cohort_size in the response is accurate;
    // enroll() returns a partial object by design (see lib/postrule-api.ts).
    const status = await getInsightsStatus(user.user_id);
    return NextResponse.json(status);
  } catch (e) {
    console.error("POST /api/insights", e);
    return NextResponse.json({ error: "internal_error" }, { status: 500 });
  }
}

export async function DELETE() {
  try {
    const user = await authedUser();
    if (!user) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
    await leaveInsights(user.user_id);
    const status = await getInsightsStatus(user.user_id);
    return NextResponse.json(status);
  } catch (e) {
    console.error("DELETE /api/insights", e);
    return NextResponse.json({ error: "internal_error" }, { status: 500 });
  }
}
