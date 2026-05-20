# Postrule roadmap

Public-facing roadmap for the open-source SDK + hosted dashboard. Updated as we ship — milestone dates are targets, not commitments. Internal strategy + financial targets are not in this doc.

For week-by-week active work, see the [issues list](https://github.com/b-tree-labs/postrule/issues). For canonical architectural decisions, see `docs/design/` (committed where the design has matured to "normative").

---

## What we've shipped

### Foundation (late 2025 / early 2026)

- The six-phase **graduation model** — RULE → MODEL_SHADOW → MODEL_PRIMARY → ML_SHADOW → ML_WITH_FALLBACK → ML_PRIMARY — designed so a classifier can climb safely from a hand-written rule to a fast in-process ML model, with the rule preserved as the safety floor at every phase
- **Statistical gate design** for phase promotion — McNemar paired-correctness, n_min thresholds, α = 0.01 default — so promotion decisions are falsifiable, not vibes-based
- Companion paper **"When Should a Rule Learn? A statistical framework for graduated ML autonomy"** drafted; eight-benchmark evaluation roster (HWU64, Banking77, CLINC150, Snips, AG News, TREC-6, and more)
- **Split licensing** locked in — open-source SDK (Apache 2.0) + hosted operator components (BSL 1.1, Change Date 2030-05-01)

### Cloud + data layer

- **Cloudflare-native production stack**: 3 Workers (api, collector, dashboard) + D1 database + CF Pages landing. Zero-ops, sub-100ms latency, per-request billing
- D1 schema covering users, api_keys, verdicts, usage_metrics, switches, cli_sessions, insights enrollments, security reports, scale indices
- Two-tier rate limiting (client SDK token bucket + server per-tier RPS caps)
- Ed25519 license-signing for offline / air-gapped Business-tier deployments

### CLI + SDK

- **16-verb CLI**: `analyze`, `init`, `bench`, `login`, `quickstart`, `roi`, `benchmark`, `plot`, `whoami`, `insights`, `mcp`, `refresh`, `doctor`, and more
- **AST-based instrumentation** via `postrule init` — wraps any Python function with `@ml_switch` without manual edits
- Device-flow CLI login with secure local credentials persistence (mirrors `gh auth login` / `flyctl auth login` UX)
- Five runnable quickstart examples bundled in the package (`hello`, `tournament`, `autoresearch`, `verifier`, `exception`)

### Dashboard

- Per-switch report cards: cost trajectory across all 6 phases, latency trajectory, McNemar gate status, drift signals, exportable audit-chain PDF
- Account overview with usage strip + recent activity
- Comp tier for partner / academic accounts (alongside Free / Pro / Scale / Business)
- Cohort-tuned defaults infrastructure ready to populate

### Release + ops discipline

- **6 PyPI releases** (1.0.0rc1 → 1.1.5) in 7 weeks, no broken-release events
- **Auto-release pipeline**: green main with a `pyproject.toml` version bump triggers PyPI publish + GitHub Release + tag. No manual ceremony, no long-lived deploy tokens
- **Branch protection** with 16 required status checks (lint, pre-commit, multi-Python test matrix, install-smoke, license-check, DCO, SPDX, coverage ratchet)
- DPA template, sub-processors page, access policy, telemetry wire-spec — published under `docs/legal/` for enterprise procurement

---

## What's next

The roadmap below is **release-themed** rather than date-themed. Some releases may land sooner; some may slip. Track active work in [issues](https://github.com/b-tree-labs/postrule/issues) and [milestones](https://github.com/b-tree-labs/postrule/milestones).

### v1.2 — Harness packs (PLG distribution)

First-class instrumentation packs for the major agentic-harness ecosystems:

- `postrule install langchain` — auto-instruments LangChain pipelines
- `postrule install crewai`, `llamaindex`, `autogen`, `dspy`, `haystack`, `instructor`, `litellm`
- Per-harness benchmarks + "tokens saved" projection in the dashboard
- One-command onboarding: from `pip install postrule` to first verdict in <60 seconds for a harness-using project

Tracking issue: [#86](https://github.com/b-tree-labs/postrule/issues/86)

### v1.3 — Cohort-tuned defaults (data flywheel)

The data flywheel goes live: cohort-aggregated priors automatically tune gate thresholds + transition windows for each new switch, so later users graduate faster than earlier users.

- Cohort-tuned default ships for the first wrapped-switch class with enough sample size
- Public benchmark showing time-to-graduation drop as cohort grows
- Opt-in cohort enrollment UX

### v1.4 — Operator-grade observability

Dashboard becomes a real ops console:

- Operator-mode (cross-account admin view) for org-level rollout
- Search + filter + sort + hot-spot highlight on the switches list (scales to 100+ instrumented sites)
- Codebase overview visualization (flame graph + heatmap)
- Phase progress indicator: click any phase badge for a graphical phase-timeline tooltip
- Per-switch git context (branch / commit / blame author)

### v1.5 — Analyzer ergonomics

`postrule analyze` becomes incremental and git-aware:

- `--diff <ref>` to surface only sites in changed files since a base
- Git-worktree-awareness (no double-counting across worktrees)
- `postrule init --branch` auto-creates a feature branch before AST mutation
- Pre-commit-friendly: dirty-tree warning, clean unified-diff output

### v1.6 — Enterprise + compliance

- SOC 2 Type 1 audit completion
- Operator SSO via Clerk (SAML / OIDC) for enterprise tenants
- Dedicated-IP email sending for transactional-mail deliverability into strict enterprise filters
- Audit-chain export improvements (CSV + JSONL alongside PDF)

### v2.0 — Multi-region + workflow-mode

Speculative; subject to community input:

- Multi-region Worker + D1 deployment for sub-25ms latency outside the US
- Workflow-mode: chained switches as a DAG, with verdicts propagating between stages
- First-class integration with at least one cloud provider's AI marketplace

---

## How to influence what we build

- **File an issue** describing the use case + the friction. Be specific.
- **+1 a label like `enhancement`** on existing issues to vote.
- **Submit a PR** with a small, scoped fix — especially welcomed on `documentation` and `good first issue` items.
- For **harness pack contributions** (`v1.2`), the schema + first reference pack will land in the repo with a `CONTRIBUTING-packs.md` walkthrough — watch the [v1.2 milestone](https://github.com/b-tree-labs/postrule/milestones) for the kick-off issue.

## Stability + breaking-change policy

- SDK semver: minor versions (`1.x → 1.y`) keep public API stable; major versions (`1.x → 2.0`) may break with a documented migration path
- Wire format versioned at the request boundary; old SDK versions keep working against new server versions for ≥ 12 months
- BSL → Apache 2.0 Change Date for hosted components: **2030-05-01**

## Where this roadmap lives

This file (`docs/strategy/roadmap.md`) is the public-facing version. It will be updated as releases ship. Internal strategy + financial targets are tracked separately and not exposed publicly.
