# Blue/green production deploys — design proposal

**Status:** proposed, not yet approved
**Author:** Benjamin Booth
**Date:** 2026-05-18
**Decides:** what "true blue/green" means for Postrule, and which subset of the textbook pattern actually maps onto our serverless stack
**Companion doc:** [docs/ops/deploy-pipeline.md](../ops/deploy-pipeline.md) (current pipeline architecture)

## Why this doc exists

Classical blue/green deploys keep two complete parallel production stacks running simultaneously: traffic flows to *green*; a new version is deployed to *blue* and validated; a load balancer atomically swaps traffic to *blue*; *green* stays warm as the instant rollback target. This pattern was designed for VM- and container-based services with stateful databases the deploys can't safely mutate.

The Postrule stack is Cloudflare-native: Workers + D1 + KV + CF Pages. Some of the textbook pattern maps cleanly. Some doesn't. The runbook ([deploy-pipeline.md](../ops/deploy-pipeline.md)) currently describes a "modern equivalent" pattern — CF Workers Versions + atomic rollback — which is operationally similar but not literally two parallel stacks. The launch bar (UT + SoilMetrix as first paying users, "we will not lose their data") requires a stronger commitment than the runbook describes.

This doc evaluates options and recommends a specific architecture.

## What "true blue/green" has to mean here

Three properties define classical blue/green, in order of how strictly each maps to our stack:

| Property | Classical | Postrule-realistic |
|----------|-----------|--------------------|
| **Atomic cutover** | LB flips traffic between stacks in one operation, no in-flight request loss | CF route reassignment (DNS) or Workers Versions traffic-split is similarly atomic. ✓ |
| **Instant rollback** | Flip the LB back, old stack still warm | CF Versions retains every prior Worker version forever; rollback is a deployment-config change, no rebuild. ✓ |
| **Pre-cutover validation against real prod resources** | Blue stack exercises real DB, real services, real secrets while green is still serving | CF Versions preview URLs hit production bindings. ✓ (currently unused; runbook needs to chain this in) |
| **Two complete parallel stacks** | Two app servers, two DB replicas, two caches | Workers: trivially parallel-deployable. D1: **not parallel-deployable** without app-layer dual-write logic. KV: parallel-deployable but eventually-consistent so split-state is observable. |

The last row is where the textbook pattern breaks down. **D1 is a single source of truth.** It can't be cleanly forked into a "blue D1" and "green D1" without writing application-level dual-write infrastructure, which is months of work and adds its own failure modes.

So we have to make a choice about what level of "true" we're willing to pay for.

## Architectural options

### Option A — Versions-only (status quo, what the runbook describes)
- One Worker, multiple Versions, percentage-based rollout
- Single D1, additive migrations only, time-travel restore as last resort
- Atomic Worker rollback via Version pin

**Pros:** zero additional infra. Works today.
**Cons:** not true blue/green. No parallel pre-cutover validation against prod bindings. D1 schema bug = no fast rollback.
**Cost:** $0 incremental.
**Verdict:** insufficient for the stated launch bar.

### Option B — Workers stacks split, D1 shared (recommended)
- Two parallel Worker names per service: e.g. `postrule-api-blue` and `postrule-api-green` (and same for collector + dashboard)
- Both bind to the **same** D1 database via `database_id`
- An "active" CF route (`api.postrule.ai → postrule-api-blue` or `→ postrule-api-green`) decides which receives prod traffic; the other receives no inbound but is warm
- Cutover = update the route binding (single API call, atomic at the edge)
- Both stacks validate against real D1, real KV, real secrets; cutover happens only after the next-version stack has been smoked
- D1 mutations are additive-only across cutovers; D1 rollback (if needed) uses time-travel

**Pros:**
- Atomic cutover with both stacks live and validated against prod bindings (matches textbook validation property)
- Instant rollback by flipping the route back
- D1 stays simple — single source of truth, no dual-write
- ~2x Worker code surface but Workers cost is negligible at our traffic
- Plays cleanly with Workers Builds (each stack is just a different Worker name in CF dashboard)

**Cons:**
- D1 schema changes must remain additive within a cutover window — same discipline as Option A, just enforced more strictly because two Worker versions are reading/writing the DB simultaneously
- Cost of duplicated KV reads (negligible)
- More CF route management overhead

**Cost:** Workers compute is per-request, so the idle warm stack doesn't add meaningful cost. The only meaningful incremental is human attention to "which color is active right now."

**Verdict:** maps onto 3 of 4 textbook properties; the 4th (parallel DB) is sacrificed deliberately because it's not worth the dual-write complexity at our stage.

### Option C — Full parallel stacks including D1 (overkill for launch)
- Everything in Option B
- Plus: `dendra-events-blue` and `dendra-events-green` D1 databases
- Writes go to BOTH databases (app-level dual-write); reads from active only
- On cutover, the new-active D1 has been verified to match the old-active
- True textbook blue/green, including DB

**Pros:** literally satisfies every textbook property; D1 schema rollback no longer time-travel-only.

**Cons:**
- Dual-write infrastructure is non-trivial to build correctly (transaction ordering, partial failures, consistency proofs)
- Storage doubles
- Cutover-window write conflicts are a real failure mode requiring careful design
- Months of engineering work for a property (DB-level instant rollback) that we can mitigate cheaper ways (additive migrations + time-travel)

**Cost:** Storage cost is real but small at our scale; engineering cost is the blocker — easily 4-6 weeks of focused work.

**Verdict:** the right answer at scale; the wrong answer for launch.

### Option D — Versions + smoke gating + gradual rollout (Option A++)
- One Worker, gradual rollout (1% → 10% → 50% → 100% over ~15min)
- Pre-cutover smoke runs against the Version preview URL (real bindings)
- Single D1, additive migrations only

**Pros:** much closer to the textbook validation property without going to two stacks.
**Cons:** still no "two complete stacks" in the classical sense. Rollback during a gradual rollout is harder than rollback after an atomic cutover.

**Verdict:** a reasonable intermediate; weaker than B on the "atomic cutover" property.

## Recommendation: Option B for launch, Option C deferred

**For the UT + SoilMetrix launch (2026-05-20 + ongoing), implement Option B.** It satisfies 3 of 4 textbook blue/green properties and the 4th is replaceable with disciplined additive migrations + D1 time-travel — both of which are launch-bar-acceptable answers for the data-durability claim.

**Defer Option C** until either:
- D1 is no longer empty (more than ~10k rows of real customer data, where time-travel restore feels like a real risk because of the data loss between restore-point and now), OR
- A customer contract requires full classical blue/green (e.g. SOC 2 Type 2 with specific resilience controls)

Either condition is months out from today.

## Implementation plan (Option B)

### Phase 1 — Foundation (before first paying user)

| Step | Owner | Estimate |
|------|-------|----------|
| Refactor `cloud/api/wrangler.toml` to declare both `postrule-api-blue` and `postrule-api-green` (different `name`, same `database_id`, same routes empty by default) | code | 1h |
| Same for `cloud/collector/wrangler.toml` | code | 30min |
| Same for `cloud/dashboard/wrangler.jsonc` | code | 30min |
| New `docs/ops/blue-green-cutover.md` runbook with the exact CF route-update API calls + smoke commands + rollback path | docs | 1h |
| Update `docs/ops/deploy-pipeline.md` Workers Builds section: each of blue/green is its own Workers Builds project, both pointing at the same repo+branch+root-dir but with different deploy targets | docs | 30min |
| Configure two Workers Builds projects per service in CF dashboard (one per color) | operator | 30min |
| First end-to-end cutover dry-run with no-op change on staging-equivalent (or against a throwaway hostname) | operator + code | 1h |
| Smoke-test suite that runs against an arbitrary base URL (not hardcoded prod hostname) so it can exercise either stack | code | 2h |

Total: ~7h of focused work, half code half ops.

### Phase 2 — Hardening (within 30 days of first paying user)

| Step |
|------|
| Add a CI check that flags `DROP TABLE` / `ALTER TABLE DROP COLUMN` / `RENAME COLUMN` in migration files for explicit human approval (enforces additive discipline) |
| Add a "two-phase migration" pattern doc with a concrete worked example |
| Add automated regression test that runs migrations against a fresh D1, applies them in order, ensures schema matches a checked-in expected dump |
| Monitoring on the inactive stack — if it's down, rollback isn't actually available; we need to know |
| Codify the cutover SLA: blue must serve traffic for N minutes (or M requests) without smoke regression before green is allowed to be drained |

### Phase 3 — Option C migration (if/when triggered)

| Trigger |
|---------|
| Customer data > 10k rows AND time-travel restore is judged unacceptable, OR |
| Compliance requirement (SOC 2 Type 2 controls) requires classical blue/green, OR |
| Two-phase migration discipline starts producing real engineering drag at >1 deploy/day cadence |

Plan: spec the dual-write architecture in a separate doc; not part of this proposal.

## Constraints worth naming explicitly

- **D1 has no native cross-database replication.** Anything involving parallel databases requires us to build dual-write logic. (CF roadmap may change this; check before Phase 3.)
- **D1 doesn't support synchronous replicas, secondaries, or geo-distributed reads.** Single primary in one region.
- **CF Workers route patterns are an account-level resource.** Both blue and green Workers can be configured but only one route binding can resolve to `api.postrule.ai` at a time. The cutover is a single API call.
- **CF Workers Versions persist forever** (no auto-eviction we've documented). Instant rollback to any prior Version stays available.
- **CF D1 time-travel is bounded to 30 days** on the Workers Paid plan. Restoring past that window is impossible.

## Open questions for the operator

These need a decision before Phase 1 starts:

1. **Naming.** "blue" and "green" or "v0" and "v1" or "even" and "odd"? Recommend "blue"/"green" — universal language, less likely to be misread as version numbers.
2. **Smoke test runner.** Local on operator laptop, GH Actions on PR-merge, or as a build step inside Workers Builds? Recommend GH Actions on PR-merge (no CF auth needed; smoke tests hit public endpoints with valid API keys).
3. **Cutover authorization.** Should cutover require two-person approval (e.g. a GitHub PR review on a `cutover/blue-to-green` branch) or is operator solo OK? Recommend solo until SOC 2 forces otherwise — speed > ceremony at this stage.
4. **Inactive-stack drainage.** Keep both warm forever, or drain the inactive stack after N hours? Recommend keep both warm — Workers cost-per-request makes idle warm essentially free.

## Non-goals

- Multi-region active/active. Single CF region per stack is fine for launch.
- Database-level transactional replication. Not without Option C.
- Worker code that detects "I'm on the inactive stack" and behaves differently. Both stacks should be identical and stateless; the route is the only authority on which is active.

## How this connects to the launch bar

The user stated:
> "I need to get the system to a point where real users can create real accounts and we can confidently say we will not lose their data."

Option B contributes by:
- Making cutover atomic so a bad deploy can't take us into a half-deployed state visible to users
- Making rollback instantaneous (single route flip) so a user-facing regression is fixable in seconds, not the minutes a `wrangler rollback` takes
- Forcing additive-migration discipline so D1 state never enters an irreversible bad state
- Adding pre-cutover smoke against real prod bindings so "deploy passed CI" is not the only check before users hit it

Combined with the existing D1 time-travel safety net (already on by default on the Workers Paid plan, 30-day window), Option B is a credible answer to "we will not lose their data" for the launch cohort.

It is not a credible answer at scale, where Option C will eventually be required. That's a planning concern, not a launch-blocker.
