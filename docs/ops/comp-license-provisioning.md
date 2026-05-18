# Comp ("Partner") license provisioning

How to put a user on the no-charge `comp` tier and how to take them off.

## What `comp` is

A complimentary / partner tier intended for:
- Internal Postrule team accounts (employees, contractors)
- Pre-launch design partners and early-access customers
- Academic / research collaborations
- Anyone using Postrule with operator approval but outside the normal billing flow

The `comp` tier shares the wire shape of every paid tier — usage is metered, rate-limited, capped, and surfaced in the dashboard identically. The only differences:
- **No Stripe subscription backs it.** Tier changes via the Stripe webhook (`subscription.created/updated/deleted`) cannot land a user on `comp` and cannot remove them from it.
- **Caps and rate limits are set by operator policy.** Currently scale-equivalent (5M verdicts/mo, 200 RPS, hard-cap, no overage), adjustable by re-tiering.
- **Dashboard billing page** renders a "Partner plan" notice instead of the plan grid.
- **Dashboard tier strip** displays `Partner` instead of a paid-tier name.

## Provisioning a user

### Prerequisites
- The user must already have an account (i.e. signed up via the dashboard and a `users` row exists)
- The user's `user_id` (integer primary key from the `users` table)
- Operator access to the `DASHBOARD_SERVICE_TOKEN` for the target environment

### Steps

```bash
# 1. Look up the user_id (production)
wrangler d1 execute dendra-events --env production --remote \
  --command "SELECT id, email, current_tier FROM users WHERE email = 'partner@example.com';"

# 2. Set tier to comp via the admin endpoint
curl -X POST https://api.postrule.ai/admin/users/$USER_ID/set-tier \
  -H "X-Dashboard-Token: $DASHBOARD_SERVICE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tier": "comp"}'

# Expected response:
# {"user_id": 42, "email": "partner@example.com", "tier": "comp", "account_hash": "..."}

# 3. Verify
wrangler d1 execute dendra-events --env production --remote \
  --command "SELECT id, email, current_tier FROM users WHERE id = $USER_ID;"
```

The user can immediately log into the dashboard and issue API keys; their existing keys (if any) continue to work at the comp-tier caps from the next request onward (the auth middleware re-reads `current_tier` on every request).

### Confirm no conflicting Stripe state

If the user previously had a Stripe subscription:
1. Cancel the subscription in the Stripe dashboard (or via Stripe CLI)
2. **Do this BEFORE setting `tier=comp`** — if a Stripe webhook fires after the tier change, it will overwrite back to the subscribed tier

```bash
# Check for an active subscription
wrangler d1 execute dendra-events --env production --remote \
  --command "SELECT status, stripe_subscription_id FROM subscriptions WHERE user_id = $USER_ID;"
```

If `status = 'active'` or `'trialing'`, cancel in Stripe first.

## Taking a user off comp

Same endpoint, different tier value:

```bash
curl -X POST https://api.postrule.ai/admin/users/$USER_ID/set-tier \
  -H "X-Dashboard-Token: $DASHBOARD_SERVICE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tier": "free"}'
```

If you want them to become a paying customer instead, the cleaner path is to **send them to the billing page** rather than manually setting a paid tier:
- They subscribe via the dashboard's normal Stripe checkout
- The webhook flips `current_tier` from `comp` → their chosen tier (Pro/Scale/Business)
- A `subscriptions` row is created with their Stripe customer + subscription IDs
- This way the billing portal works for them going forward

## Re-tiering a comp user

If a legitimate comp user is hitting the 5M/mo hard-cap and you want to give them more headroom:

```bash
# Option A: bump to business-equivalent caps (25M/mo, 1000 RPS) but stay no-charge
# (currently no `comp-business` tier exists — would need to add one if we want
# per-comp-user variable caps; for now the operator either bumps to a paying
# tier or accepts the 5M cap as policy)
```

If unlimited / very-high cap is needed, the cleanest option today is to add a per-user override on `users.rate_limit_rps_override` and accept the 5M monthly cap as policy. If many comp users need higher caps, consider adding a second comp variant (`comp-business`) to the tier enum.

## Auditing

```bash
# List all comp-tier users (production)
wrangler d1 execute dendra-events --env production --remote \
  --command "SELECT id, email, created_at, updated_at FROM users WHERE current_tier = 'comp' ORDER BY updated_at DESC;"

# Their usage this month
wrangler d1 execute dendra-events --env production --remote \
  --command "
    SELECT u.email, um.period, um.classifications_count, um.overage_classifications
      FROM usage_metrics um
      JOIN api_keys k ON k.id = um.api_key_id
      JOIN users u ON u.id = k.user_id
     WHERE u.current_tier = 'comp'
       AND um.period = strftime('%Y-%m', 'now')
     ORDER BY um.classifications_count DESC;"
```

## Security considerations

- The `/admin/users/:user_id/set-tier` endpoint is gated on `DASHBOARD_SERVICE_TOKEN` (same constant-time bearer comparison as every other `/admin/*` endpoint). Anyone with the token can re-tier any user.
- Rotate `DASHBOARD_SERVICE_TOKEN` if it's ever exposed (also requires updating `POSTRULE_API_SERVICE_TOKEN` on the dashboard Worker since they share the value).
- The endpoint logs to Workers Observability (`console.log`) per the standard admin-endpoint pattern — provides 3-day audit trail of tier changes.

## When NOT to use comp

- Customer wants to evaluate before paying → use the standard Free tier (10K verdicts/mo, no card required). Comp is for relationships, not free trials.
- Internal CI / synthetic load → use a dedicated test account, not comp on a real user.
- Anyone whose deal is uncertain → leave them on Free until you have a verbal yes.
