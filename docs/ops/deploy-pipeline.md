# Deploy pipeline

How Postrule code reaches production, and what to touch when something breaks.

## Architecture

```
┌─────────────────┐  push to main    ┌─────────────────────┐  deploys to   ┌──────────────────────┐
│  b-tree-labs/   │ ───────────────► │ CF Workers Builds   │ ───────────►  │  postrule-collector- │
│  postrule (GH)  │                  │  (collector-staging)│               │  staging Worker      │
└────────┬────────┘                  └─────────────────────┘               │  + dendra-events-    │
         │                                                                  │  staging D1 migrate  │
         │ push to release          ┌─────────────────────┐               └──────────────────────┘
         └─────────────────────────►│ CF Workers Builds   │  deploys to    ┌──────────────────────┐
                                    │  (collector-prod)   │ ───────────►   │  postrule-collector  │
                                    └─────────────────────┘                │  Worker + dendra-    │
                                                                            │  events D1 migrate   │
                                                                            └──────────────────────┘

┌─────────────────┐  push to main    ┌─────────────────────┐
│  landing/ dir   │ ───────────────► │  CF Pages           │ ───────► postrule.ai (prod)
│  in same repo   │                  │  git integration    │ ───────► staging.postrule.ai (preview)
└─────────────────┘                  └─────────────────────┘
```

**Auth model:** no long-lived secrets in GitHub. Cloudflare Workers Builds and CF Pages each maintain their own short-lived deploy credentials, scoped to the project. The GitHub-side connection is a OAuth-style app authorization, revocable at any time from either side.

**Promotion gate:** `main` auto-deploys staging. Production deploys only when a human merges `main → release`. No tag-push trigger; the gate is a deliberate cross-branch merge.

## One-time setup

### Prerequisites
- Cloudflare account with Workers Paid plan (Workers Builds is included)
- GitHub org-admin on `b-tree-labs`
- The Cloudflare GitHub App will need to be installed on the org

### Step 1 — Install Cloudflare GitHub App (5 min)

1. Cloudflare dashboard → **Workers & Pages** → any Worker → **Settings** → **Build** → **Connect**
2. Pick **GitHub** as the provider
3. CF redirects you to GitHub to authorize the Cloudflare GitHub App
4. Install on the `b-tree-labs` org; scope to `postrule` repo only (least-privilege)
5. Confirm back in the CF dashboard

This authorizes CF to read the repo and write deployment statuses. It does NOT grant CF write access to the repo.

### Step 2 — Create Workers Builds project for staging (10 min)

1. Cloudflare dashboard → **Workers & Pages** → **Create application** → **Worker**
2. Choose **Connect to Git**, select `b-tree-labs/postrule`
3. Worker name: `postrule-collector-staging`
4. Production branch: `main`
5. **Build settings:**
   - Root directory: `cloud/collector`
   - Build command: *(leave empty — no JS/TS bundling needed; wrangler handles it)*
   - Deploy command:
     ```
     npx wrangler d1 migrations apply dendra-events-staging --remote --yes && npx wrangler deploy
     ```
6. Save and trigger a first build

The first build will reuse whatever is currently live; if it succeeds, staging is wired up.

### Step 3 — Create Workers Builds project for production (10 min)

1. Same flow as Step 2
2. Worker name: `postrule-collector` *(matches the existing prod Worker so the new pipeline takes over the same script)*
3. Production branch: **`release`** *(not main — this is the prod gate)*
4. **Build settings:**
   - Root directory: `cloud/collector`
   - Deploy command:
     ```
     npx wrangler d1 migrations apply dendra-events --env production --remote --yes && npx wrangler deploy --env production
     ```
5. Before triggering the first build, create the `release` branch from `main`:
   ```
   git checkout main && git pull
   git checkout -b release
   git push -u origin release
   ```
6. The push to `release` will trigger the first prod build

### Step 4 — CF Pages for landing (5 min)

The `landing/` directory is the marketing site; it deploys to CF Pages, separate from Workers.

1. Cloudflare dashboard → **Workers & Pages** → **Create application** → **Pages**
2. Connect to Git, select `b-tree-labs/postrule`
3. Project name: `postrule-landing`
4. Production branch: `main`
5. **Build settings:**
   - Build command: *(empty — static HTML/CSS, no build step)*
   - Build output directory: `landing`
6. Custom domain: `postrule.ai` (set after first successful deploy)

CF Pages serves preview deploys for every PR/branch out of the box at `<branch>.<project>.pages.dev`.

### Step 5 — Verify (5 min)

After staging and prod Workers Builds + Pages are configured:

```bash
# Verify staging deploys on main push
git checkout main && git commit --allow-empty -m "chore: verify staging deploy pipeline"
git push
# Watch the CF dashboard build log; should complete in ~2-3 min

# Verify prod gate is closed (no auto-deploy)
# (no action — confirm by checking that postrule-collector dashboard
#  shows no new deploy after the main push)

# Verify prod gate works when intentionally triggered
git checkout release && git merge main && git push
# Watch CF dashboard; postrule-collector should build and deploy

# Verify D1 schema after deploy
cd cloud/collector
wrangler d1 migrations list dendra-events --env production --remote
# Should report "No migrations to apply"
```

## Steady-state operation

### Shipping a change to staging only

1. Open a PR against `main`
2. CI checks must pass (lint, tests, pre-commit, etc.)
3. Merge to `main`
4. CF Workers Builds auto-deploys to `postrule-collector-staging` (~2-3 min)
5. CF Pages auto-deploys `landing/` to `postrule.ai` (~1-2 min)
6. Verify staging at `staging-collector.postrule.ai/health`

### Promoting staging to production

1. Verify staging has been live and healthy for at least *(your call — minutes for low-risk, hours for high-risk changes)*
2. Merge `main → release`:
   ```bash
   git checkout release
   git pull
   git merge --ff-only main  # fast-forward — fails if release has diverged
   git push
   ```
3. CF Workers Builds auto-deploys to `postrule-collector` (~2-3 min including D1 migration)
4. Verify prod at `collector.postrule.ai/health` + run smoke tests

### Smoke tests

Live in `cloud/collector/tests/smoke/`. Run after any prod deploy:

```bash
cd cloud/collector
DENDRA_SMOKE_BASE=https://collector.postrule.ai npm test -- smoke
```

These tests must not depend on a CF API token — they only need to hit public endpoints with valid API keys (which live in `~/.postrule/credentials` on the operator machine).

## D1 migrations

### Adding a new migration

1. Create `cloud/collector/migrations/00NN_descriptive_name.sql`
2. Always guard table/column/index creation with `IF NOT EXISTS` for re-runnability
3. PR + merge → staging applies it automatically as part of the staging build
4. Verify on staging before promoting to release

### Migration application timing

`wrangler d1 migrations apply` runs BEFORE `wrangler deploy` in the Workers Builds deploy command (see the `&&` chain in Step 2/3). If a migration fails, the deploy is aborted — the Worker continues running against the unchanged schema. This is intentional: we never want a Worker version that expects schema changes that didn't apply.

If a migration succeeds but the deploy fails (e.g. Worker code rejected for syntax error), the schema is now ahead of the running code. SQLite tolerates this for additive migrations (new tables/columns/indexes the old code ignores). For breaking changes, follow the **two-phase migration** pattern:
1. Phase A migration: add new schema additively, old code keeps working
2. Deploy code that reads/writes both old + new
3. Phase B migration (next deploy): drop old schema

### Manual migration application

Avoid this — it desyncs `d1_migrations` tracking from reality. If you must (recovery scenario), use:

```bash
wrangler d1 execute dendra-events --env production --remote --file=migrations/00NN_xxx.sql
# Then sync the tracker:
wrangler d1 execute dendra-events --env production --remote \
  --command "INSERT INTO d1_migrations (name, applied_at) VALUES ('00NN_xxx.sql', datetime('now'));"
```

(This is the recovery path we used on 2026-05-18 to fix tracking desync inherited from the rename sprint — see backup at `~/Postrule-DB-Backups/dendra-events-prod-pre-0005-2026-05-18.sql`.)

## Rollback

### Worker rollback (fast, no D1 changes)

CF dashboard → Worker → **Deployments** tab → find prior version → **Promote**.

Or via CLI:
```bash
cd cloud/collector
wrangler rollback --env production --message "rollback to <commit-sha>"
```

This swaps the Worker version atomically. Takes < 30 seconds. Does NOT roll back D1 schema.

### D1 rollback (slow, manual)

D1 doesn't support automatic schema rollback. Strategies in order of preference:

1. **Roll forward**: write a new migration that reverses the bad change. Preferred.
2. **Manual revert**: `wrangler d1 execute` with explicit `DROP TABLE` / `ALTER TABLE DROP COLUMN`. Update tracker. Dangerous; only for additive migrations that can be cleanly reversed.
3. **Restore from backup**: re-import the last `wrangler d1 export` SQL into a fresh D1 db, swap bindings. Last resort; loses any data written since the backup.

Take a `wrangler d1 export` before any destructive migration.

## Backups

Cloudflare D1 has automatic point-in-time recovery to any second in the last 30 days (Workers Paid plan). To restore:

```bash
wrangler d1 time-travel restore dendra-events --env production --remote --timestamp <ISO-8601>
```

For manual snapshots (e.g. before risky migrations):

```bash
mkdir -p ~/Postrule-DB-Backups
wrangler d1 export dendra-events --env production --remote \
  --output ~/Postrule-DB-Backups/dendra-events-prod-$(date -u +%Y-%m-%dT%H%M%SZ).sql
```

Backups are SQL files; not encrypted at rest. Store on operator-controlled disk only.

## If Workers Builds itself is down

Cloudflare publishes Workers Builds availability at https://www.cloudflarestatus.com.

Manual deploy fallback (requires operator with `wrangler login`'d local environment):

```bash
cd cloud/collector
wrangler d1 migrations apply dendra-events --env production --remote
wrangler deploy --env production
```

This bypasses the pipeline entirely. Update `docs/ops/incident-log.md` (when that exists) with what happened and why.

## Retired pipelines

The previous deploy pipeline used GitHub Actions with a long-lived `CLOUDFLARE_API_TOKEN` secret. It was retired on *(this date — fill in after Workers Builds is live)* because:

- The static token was a persistent secret-management burden
- The token's scope didn't follow the dendra.run → postrule.ai zone rename, causing 5+ consecutive staging-deploy failures
- The production-deploy trigger pattern (`release-*` tags) never matched actual tag names (`v*`), so production had never auto-deployed via the workflow

The retired workflows lived at:
- `.github/workflows/deploy-staging.yml`
- `.github/workflows/deploy-production.yml`

Removed in PR *(fill in after retire PR merges)*.
