// Copyright (c) 2026 B-Tree Labs
// SPDX-License-Identifier: LicenseRef-BSL-1.1
//
// Server-side client for the Postrule api Worker's /admin endpoints.
// Used by the dashboard's Clerk-authenticated route handlers; never
// runs on the client (the service token must stay on the server).

const API_BASE = process.env.POSTRULE_API_BASE_URL ?? 'https://staging-api.postrule.ai';
const SERVICE_TOKEN = process.env.POSTRULE_API_SERVICE_TOKEN;

function assertServerOnly() {
  if (typeof window !== 'undefined') {
    throw new Error('postrule-api lib is server-only — service token must not leak to the client');
  }
  if (!SERVICE_TOKEN) {
    throw new Error('POSTRULE_API_SERVICE_TOKEN env var is not set');
  }
}

async function adminFetch<T>(path: string, init?: RequestInit): Promise<T> {
  assertServerOnly();
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      'X-Dashboard-Token': SERVICE_TOKEN!,
      ...(init?.headers ?? {}),
    },
    cache: 'no-store',
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`postrule-api ${path} failed: ${res.status} ${text}`);
  }
  return res.json() as Promise<T>;
}

/**
 * Variant of adminFetch that surfaces a 404 as `null` instead of throwing.
 * Used by report-card lookups where "switch not found for this user" is
 * a routine outcome (we render Next.js notFound()).
 */
async function adminFetchNullable<T>(
  path: string,
  init?: RequestInit,
): Promise<T | null> {
  assertServerOnly();
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      'X-Dashboard-Token': SERVICE_TOKEN!,
      ...(init?.headers ?? {}),
    },
    cache: 'no-store',
  });
  if (res.status === 404) return null;
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`postrule-api ${path} failed: ${res.status} ${text}`);
  }
  return res.json() as Promise<T>;
}

export interface PostruleUser {
  user_id: number;
  tier: 'free' | 'pro' | 'scale' | 'business' | 'comp';
  account_hash: string;
}

export interface ApiKeyMeta {
  id: number;
  key_prefix: string;
  key_suffix: string;
  name: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

export interface IssuedKey {
  id: number;
  plaintext: string;
  prefix: string;
  suffix: string;
  name: string | null;
  environment: 'live' | 'test';
  created_at: string;
}

/** Idempotent — call on every authenticated request. Returns the user's row id. */
export async function upsertUser(clerkUserId: string, email: string): Promise<PostruleUser> {
  return adminFetch<PostruleUser>('/admin/users', {
    method: 'POST',
    body: JSON.stringify({ clerk_user_id: clerkUserId, email }),
  });
}

export async function listKeys(userId: number): Promise<ApiKeyMeta[]> {
  const r = await adminFetch<{ keys: ApiKeyMeta[] }>(`/admin/keys?user_id=${userId}`);
  return r.keys;
}

export async function issueKey(
  userId: number,
  name: string | null = null,
  environment: 'live' | 'test' = 'live',
): Promise<IssuedKey> {
  return adminFetch<IssuedKey>('/admin/keys', {
    method: 'POST',
    body: JSON.stringify({ user_id: userId, name, environment }),
  });
}

export async function revokeKey(userId: number, keyId: number): Promise<void> {
  await adminFetch(`/admin/keys/${keyId}`, {
    method: 'DELETE',
    body: JSON.stringify({ user_id: userId }),
  });
}

// ---------------------------------------------------------------------------
// CLI device-flow admin endpoints. Companions to /v1/device/* on the api
// Worker; called from the dashboard's /cli-auth page after Clerk auth.
// ---------------------------------------------------------------------------

export type CliSessionState =
  | 'pending'
  | 'authorized'
  | 'denied'
  | 'consumed'
  | 'expired';

export interface CliSessionInfo {
  state: CliSessionState;
  device_name: string | null;
  created_at: string;
  expires_at: string;
  authorized_at: string | null;
}

export async function lookupCliSession(userCode: string): Promise<CliSessionInfo> {
  return adminFetch<CliSessionInfo>(
    `/admin/cli-sessions/${encodeURIComponent(userCode)}`,
  );
}

export async function authorizeCliSession(
  userCode: string,
  userId: number,
): Promise<void> {
  await adminFetch(
    `/admin/cli-sessions/${encodeURIComponent(userCode)}/authorize`,
    {
      method: 'POST',
      body: JSON.stringify({ user_id: userId }),
    },
  );
}

export async function denyCliSession(userCode: string): Promise<void> {
  await adminFetch(
    `/admin/cli-sessions/${encodeURIComponent(userCode)}/deny`,
    {
      method: 'POST',
    },
  );
}

// ---------------------------------------------------------------------------
// Dashboard root-page data — the tier+usage strip and recent-activity feed
// both pull from these. Errors are surfaced as null so a single failed call
// degrades gracefully instead of taking the whole page down.
// ---------------------------------------------------------------------------

export interface UsageInfo {
  tier: 'free' | 'pro' | 'scale' | 'business' | 'comp';
  verdicts_this_period: number;
  cap: number | null;
  period_start: string;
  period_end: string;
}

export async function getUsage(userId: number): Promise<UsageInfo> {
  return adminFetch<UsageInfo>(`/admin/usage?user_id=${userId}`);
}

export interface RecentVerdict {
  id: number;
  switch_name: string;
  phase: string | null;
  rule_correct: number | null;
  model_correct: number | null;
  ml_correct: number | null;
  created_at: string;
}

export async function listRecentVerdicts(
  userId: number,
  limit = 5,
): Promise<RecentVerdict[]> {
  const r = await adminFetch<{ verdicts: RecentVerdict[] }>(
    `/admin/verdicts/recent?user_id=${userId}&limit=${limit}`,
  );
  return r.verdicts;
}

// ---------------------------------------------------------------------------
// Preferences + insights enrollment (cloud/api/src/preferences.ts).
// Backs the /dashboard/settings and /dashboard/insights pages.
// ---------------------------------------------------------------------------

export interface PostrulePreferences {
  user_id: number;
  email: string;
  display_name: string | null;
  telemetry_enabled: boolean;
  tier: 'free' | 'pro' | 'scale' | 'business' | 'comp';
  account_hash: string;
}

export async function getPreferences(userId: number): Promise<PostrulePreferences> {
  return adminFetch<PostrulePreferences>(`/admin/whoami?user_id=${userId}`);
}

export interface PreferencesPatch {
  display_name?: string | null;
  telemetry_enabled?: boolean;
}

export async function patchPreferences(
  userId: number,
  patch: PreferencesPatch,
): Promise<PostrulePreferences> {
  return adminFetch<PostrulePreferences>('/admin/whoami', {
    method: 'PATCH',
    body: JSON.stringify({ user_id: userId, ...patch }),
  });
}

export interface InsightsStatus {
  enrolled: boolean;
  enrolled_at: string | null;
  last_sync_at: string | null;
  cohort_size: number;
}

export async function getInsightsStatus(userId: number): Promise<InsightsStatus> {
  return adminFetch<InsightsStatus>(`/admin/insights/status?user_id=${userId}`);
}

export async function enrollInsights(userId: number): Promise<InsightsStatus> {
  const r = await adminFetch<{
    enrolled: boolean;
    enrolled_at: string | null;
    last_sync_at: string | null;
  }>(`/admin/insights/enroll`, {
    method: 'POST',
    body: JSON.stringify({ user_id: userId }),
  });
  // The enroll endpoint doesn't echo cohort_size to keep its response
  // tight; the page re-fetches status after a successful toggle to pick
  // up the new value. Surface a partial object here for callers that
  // want it inline.
  return { ...r, cohort_size: 0 };
}

export async function leaveInsights(userId: number): Promise<void> {
  await adminFetch(`/admin/insights/leave`, {
    method: 'POST',
    body: JSON.stringify({ user_id: userId }),
  });
}

// ---------------------------------------------------------------------------
// Switches roster + per-switch report-card admin proxy. Counterpart of the
// /admin/switches/* surface in cloud/api/src/admin.ts.
// ---------------------------------------------------------------------------

export interface SwitchSummary {
  switch_name: string;
  current_phase: string | null;
  current_phase_label: string | null;
  total_verdicts: number;
  first_activity: string;
  last_activity: string;
  sparkline: number[];
  // Null when the switch is not archived. ISO 8601 timestamp otherwise.
  archived_at: string | null;
  archived_reason: string | null;
}

export interface SwitchListResponse {
  switches: SwitchSummary[];
  sparkline_window_days: number;
  // Count of archived switches the user owns — surfaced regardless of
  // the include_archived flag so the UI can decide whether to render
  // the "Show archived (N)" toggle in the table header.
  archived_count: number;
}

export interface SwitchReportAgg {
  total: number;
  rule_total: number;
  rule_correct: number;
  model_total: number;
  model_correct: number;
  ml_total: number;
  ml_correct: number;
  paired_total: number;
  b: number;
  c: number;
  first_at: string | null;
  last_at: string | null;
}

export interface SwitchPhaseDistribution {
  phase: string | null;
  n: number;
}

export interface SwitchTransition {
  phase: string;
  first_seen: string;
  last_seen: string;
  n: number;
}

export interface SwitchReport {
  switch_name: string;
  days: number;
  agg: SwitchReportAgg;
  phases: SwitchPhaseDistribution[];
  transitions: SwitchTransition[];
  current_phase: string | null;
  current_phase_label: string | null;
  mcnemar_p_two_sided: number | null;
  // Archive state. Null when the switch is not archived — the per-switch
  // page uses this to decide whether to render the archived banner vs.
  // the stale-detection banner (mutually exclusive).
  archived_at: string | null;
  archived_reason: string | null;
}

export async function listSwitches(
  userId: number,
  includeArchived = false,
): Promise<SwitchListResponse> {
  const params = new URLSearchParams({ user_id: String(userId) });
  if (includeArchived) params.set('include_archived', 'true');
  return adminFetch<SwitchListResponse>(`/admin/switches?${params.toString()}`);
}

/** Returns null on 404 (user does not own this switch). */
export async function getSwitchReport(
  userId: number,
  switchName: string,
  days = 30,
): Promise<SwitchReport | null> {
  const safeName = encodeURIComponent(switchName);
  return adminFetchNullable<SwitchReport>(
    `/admin/switches/${safeName}/report?user_id=${userId}&days=${days}`,
  );
}

// ---------------------------------------------------------------------------
// Archive / unarchive — customer-driven hide/show. Available on all tiers.
// Idempotent: re-archiving returns the existing row, re-unarchiving is 200.
// Auto-unarchive on next verdict happens server-side (see verdicts.ts).
// ---------------------------------------------------------------------------

export interface SwitchArchive {
  id: number;
  user_id: number;
  switch_name: string;
  archived_at: string;
  archived_reason: string | null;
}

export async function archiveSwitch(
  userId: number,
  switchName: string,
  reason?: string | null,
): Promise<SwitchArchive> {
  const safeName = encodeURIComponent(switchName);
  const r = await adminFetch<{ archive: SwitchArchive }>(
    `/admin/switches/${safeName}/archive`,
    {
      method: 'POST',
      body: JSON.stringify({ user_id: userId, reason: reason ?? null }),
    },
  );
  return r.archive;
}

export async function unarchiveSwitch(
  userId: number,
  switchName: string,
): Promise<void> {
  const safeName = encodeURIComponent(switchName);
  await adminFetch(`/admin/switches/${safeName}/unarchive`, {
    method: 'POST',
    body: JSON.stringify({ user_id: userId }),
  });
}
