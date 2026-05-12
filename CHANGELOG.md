# Changelog

All notable changes to Dendra are documented in this file. Format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Version numbers follow [Semantic Versioning](https://semver.org).

## [Unreleased]

_Nothing yet. Post-launch work tracks here._

## [1.1.0] — 2026-05-20

The first public release. Skips 1.0.0 deliberately to match the
product posture explicitly ("software smarter every month than
the day you shipped it") — the .0.0 milestone weight reads as
"this is frozen now," which it isn't and won't be. 1.0.0rc1 was
the pre-release tag on PyPI; 1.1.0 supersedes it for the public
ship. Subsequent iteration releases as 1.1.x / 1.2.0 / etc.,
each version increment a measurable improvement rather than a
re-coronation.

Ships ahead of the companion paper *"When should a rule learn?
A statistical framework for graduated ML autonomy"* (arXiv
submission targeted ~2026-05-22, a few days after launch).

### Launch-push (2026-05-11)

26 PRs landed on 2026-05-11 to take the v1.0 SaaS surface from "honest
but rough" to "honest and shippable." Public launch slipped 7 days
(2026-05-13 → **2026-05-20**); arXiv decoupled to ~2026-05-22 to
pair with the Snips-rerun + paper-polish work.

#### Added

- **Aggregator publishes cohort defaults to Cloudflare KV** (#27).
  The nightly `aggregator.yml` workflow now writes
  `tuned-defaults.json` to a versioned KV namespace instead of
  pushing to `main` — separates code from data.
- **Landing signup CTAs** (#28). Nav pill, hero secondary button,
  end-of-install-block CTA. Three high-leverage paths from landing
  into account creation.
- **Telemetry pipeline (SDK → cloud)** (#31). End-to-end verdict-
  event pipeline: count-only events (switch_name, phase, paired-
  correctness, timestamp, HMAC-SHA256 of credential), cloud
  `/v1/events` accepts + persists, aggregator rolls into per-tenant
  `/v1/usage`. Default-on for signed-in users; three opt-outs
  honored (`DENDRA_NO_TELEMETRY=1`, per-switch `telemetry=False`,
  `/dashboard/settings` toggle).
- **`dendra analyze` M3 login nudge** (#33). One-line invite to
  sign up at `dendra.run` after a successful analyze run.
  Suppressible via `--no-nudge`; honored across stdout + JSON.
- **Real `/dashboard` root** (#35). Tier-aware tier+usage strip,
  M5 onboarding checklist, recent-activity feed, A7 earned-upgrade
  banner. Replaces the prior two-admin-card shim.
- **`/dashboard/insights` cohort toggle + `/dashboard/settings`
  telemetry+account** (#36). Insights toggle gates cohort
  contribution; settings exposes the telemetry-default toggle and
  a delete-via-mailto account-deletion path (DELETE endpoint in v1.1).
- **`/dashboard/switches` list + per-switch report-card page**
  (#37). Cockpit view across every wrapped switch in an account
  plus a per-switch card that mirrors the markdown report-card shape.
- **SDK honors `/v1/whoami.telemetry_enabled` in `maybe_install`**
  (#38). Dashboard toggle takes effect without a second SDK call.
- **Stale-switch detection + manual archive UX** (#41). Migration
  0008 (`switch_archives`), idempotent `/admin/switches/:name/{archive,unarchive}`,
  auto-unarchive on next verdict via single indexed DELETE in the
  POST `/v1/verdicts` hot path. Dashboard: stale chip (>30d, not
  archived), archived chip, "Show archived" toggle, archived/stale
  banners, inline archive form.
- **Chaos + fault-injection cases for new dashboard surface** (#42).
  Failure-injection coverage across the dashboard API surface.
- **Scale harness for dashboard endpoints + hot path** (#44).
  Skip-by-default (`DENDRA_SCALE=1`) vitest-pool-workers harness:
  50 users / 76 keys / 748k verdicts. Headline: hot-path POST
  `/v1/verdicts` serial p99=14 ms; concurrent p99=304 ms (contention
  on usage_metrics UPSERT, not the new auto-unarchive DELETE).
- **Procurement-readiness artifacts under `docs/legal/`** (#47).
  DPA template (GDPR Art. 28 + Module 2 SCCs by reference + Texas
  governing law), sub-processors list (Cloudflare/Clerk/Stripe/GitHub
  with role-allocation honesty on BYOK), access policy (24h
  compelled-disclosure notice + 72h breach SLA + transparency-report
  cadence + §8 catalogue of unmade claims), telemetry wire spec
  (sourced from `verdict_telemetry._build_payload` + programmatic
  verification recipe + 90-day deprecation policy).

#### Changed

- **Pricing restructure: collapse free tiers, BYOK judge → Pro,
  "Verdicts / mo" metering** (#29). Single Free tier replaces the
  prior Free / Starter / OSS split. BYOK judge orchestration moved
  from Free to Pro. Metering unit renamed `Classifications / mo`
  → `Verdicts / mo` across landing, dashboard, and Stripe metadata.
- **Dashboard brand port + v1.0 privacy contract** (#30). Landing-
  page brand tokens copied into `cloud/dashboard/app/globals.css`;
  Space Grotesk + Geist Mono via `next/font/google`; wordmark +
  glyph header on every page. Privacy page rewritten around the
  v1.0 telemetry contract (unauthenticated → nothing; signed-in →
  count-only verdict events default-on; three opt-outs honored;
  richer per-switch data is opt-in only; retention Free 7d / Pro 30d
  / Scale 90d / Business 1y).
- **Paper polish + concrete citations + submission-ready title page**
  (#34). Title page reflowed to camera-ready form; placeholder
  citations resolved to concrete entries in
  `related-work-bibliography.md`; abstract sharpened.
- **Soften Business-tier SOC 2 claim to a roadmap commitment** (#45).
  `landing/data/pricing-tiers.json` now reads "SOC 2 Type 1 target
  Q4 2026" rather than asserting current SOC 2 readiness.
- **Cloud surface now in security-policy scope; cross-link procurement
  docs** (#48). `SECURITY.md` extended to cover the dashboard +
  API Worker surface; links to the four `docs/legal/` artifacts.
- **`/admin/switches` N+1 current_phase subquery replaced with
  FIRST_VALUE window** (#49). Wire shape unchanged; heavy-tail
  user adjusted p99 drops from ~140-150 ms to <20 ms.
- **Cross-surface dashboard polish pass + brand-token unification**
  (#40). Stray `text-neutral-600` → brand tokens; Docs link added
  to footer for parity with landing; UserButton hoisted into global
  header behind `SignedIn`; `Promise.all` → `Promise.allSettled`
  on `/dashboard` with graceful per-section degradation; tier-
  agnostic "Get started" eyebrow replacing Free-plan-only copy;
  voice consistency (`function` → `call site`, `events` → `verdicts`).
- **Post-Phase-1 documentation consistency pass + licensing FAQ**
  (#39). Two new FAQ entries on reseller-library and acquisition
  postures; date sweep 2026-05-13 → 2026-05-20.

#### Fixed

- **Snips ML head degenerate at 1k checkpoint** (#32). The
  `SklearnTextHead._MonoclassPredictor` no-op path collapsed the
  Snips ML rule to predict-the-modal-class at the 1k-row checkpoint
  (~14.3%). Root-caused to a build-order interaction in
  `build_reference_rule` when the seed slice contained one label
  after sort. Fix forces multi-class fit at the head level. Table 3
  in the companion paper corrected with the rerun numbers.
- **Cap KV cohort-size read at 100ms; document half-count trade-off**
  (#43). Bounds the worst-case latency of the cohort-defaults
  cold-read; documents the half-count behavior under timeout.

#### Security

- **Pre-launch security audit + small-fix follow-ups** (#46).
  Cross-surface audit (SDK, cloud API, dashboard, CI/CD, secret
  hygiene); response headers tightened on the dashboard middleware
  (CSP, HSTS, X-Frame-Options DENY, Permissions-Policy); small
  fixes folded in.

#### Release

- **Bump version 1.0.0rc1 → 1.1.0 (skip 1.0.0)** (#51). The .0.0
  milestone weight reads as "this is frozen now," which it isn't
  and won't be. 1.0.0rc1 was the pre-release tag on PyPI; 1.1.0
  supersedes it for the public ship.

#### Documentation

- **Strict pre-arXiv accuracy review on the companion paper** (#50).
  Adds `docs/papers/2026-when-should-a-rule-learn/ACCURACY_REVIEW-2026-05-11.md`
  (5 must-fix, 8 should-fix, 4 nice-to-have findings + verified-clean
  section). One mechanical fix applied to `paper-draft.md` (stale
  2026-05-13 → ~2026-05-22).

### Highlights

- **Six-phase graduated-autonomy lifecycle** (`Phase.RULE` →
  `Phase.MODEL_SHADOW` → `Phase.MODEL_PRIMARY` → `Phase.ML_SHADOW`
  → `Phase.ML_WITH_FALLBACK` → `Phase.ML_PRIMARY`) with paired-
  McNemar statistical gates at every transition.
- **Rule safety floor** retained throughout. `safety_critical=True`
  refuses construction in any phase without a rule fallback;
  the circuit breaker auto-reverts ML decisions to the rule on
  failure and persists across process restart when `persist=True`.
- **`CandidateHarness`** for autoresearch / agent-loop integration.
  Shadow candidate classifiers against live production, run paired-
  McNemar significance tests against a truth oracle, return
  promotion verdicts. The production substrate that turns "the
  loop suggests it's better" into "the evidence justifies the
  swap."
- **Native async API** — `aclassify`, `adispatch`, `arecord_verdict`,
  `abulk_record_verdicts`. Async LLM adapter siblings
  (`OpenAIAsyncAdapter`, `AnthropicAsyncAdapter`,
  `OllamaAsyncAdapter`, `LlamafileAsyncAdapter`). Committee
  judging via `asyncio.gather` for parallel-LLM verdicts.
- **VerdictSource family** — `CallableVerdictSource`,
  `LLMJudgeSource` (with self-judgment bias guardrail referencing
  G-Eval / MT-Bench / Arena literature), `LLMCommitteeSource`
  (majority / unanimous aggregation), `WebhookVerdictSource`,
  `HumanReviewerSource`.
- **Bulk ingestion** — `bulk_record_verdicts`,
  `bulk_record_verdicts_from_source`, `export_for_review` /
  `apply_reviews` for reviewer round-trip.
- **Storage backends** — `BoundedInMemoryStorage` (default),
  `InMemoryStorage`, `FileStorage` (POSIX flock + optional fsync
  + batched-async mode), `SqliteStorage` (WAL),
  `ResilientStorage` (auto-fallback wrapper). Pluggable `redact=`
  hook at the storage boundary for HIPAA / PII workloads.
- **Production performance** — 33 µs p50 classify on the
  `persist=True` recommended path (batched FileStorage), 195 µs
  p50 with per-call fsync durability, 0.5 µs p50 at Phase 0
  with `auto_record=False`.
- **Paired-McNemar transition depth** ≤ 250 outcomes across
  ATIS / HWU64 / Banking77 / CLINC150 — every benchmark clears
  paired statistical significance at the first checkpoint.
  Tighter than the previously-published unpaired-z-test depths
  in the same literature.

### Key contributions vs prior art

- Production-deployment substrate for autoresearch loops — every
  primitive needed (shadow phases, statistical gate, rule floor,
  audit chain, async committee judging, redaction hooks) ships
  in one library. See `docs/autoresearch.md` for the positioning.
- Tighter paired-McNemar transition depth (≤ 250 outcomes across
  4 NLU benchmarks) compared to the unpaired-z-test results in
  earlier cascade-routing literature.
- Architectural rule-floor guarantee. `safety_critical=True`
  refuses construction in any phase without a rule fallback;
  cannot be removed without a code change.

### Breaking changes from v0.2.x

The v0.2.x release was internal-only; any downstream code from a
private v0.2.x snapshot would need to handle:

- `record_prediction` → `record_verdict`.
- `Phase.LLM_*` → `Phase.MODEL_*`.
- `ClassificationResult.output` → `.label`;
  `ClassificationRecord.output` → `.label`.
- `auto_record=True` is now the default on `LearnedSwitch` —
  classify / dispatch auto-append an UNKNOWN outcome record.
  Pass `auto_record=False` to suppress.
- `auto_advance=True` is now the default — `record_verdict`
  triggers `advance()` every `auto_advance_interval` records
  (default 500).
- `LearnedSwitch` now uses `__slots__` — subclasses adding
  attributes must declare their own `__slots__`.

### Statistics

- 473 tests passing, 4 skipped (require optional extras).
- 19 runnable examples (`examples/01_hello_world.py` through
  `examples/19_autoresearch_loop.py`).
- 0 hard runtime dependencies. LLM adapters, ML head, and
  benchmarks are optional extras.

### License

- Client SDK: Apache-2.0.
- Analyzer / server / dashboards: BSL-1.1 with Change Date
  2030-05-01 (production self-hosted use is permitted by the
  license; competing-hosted-service is prohibited).

### Brand identity system

The full v1.0.0 release also ships the public brand identity
work that landed during the pre-launch period:

- **Extended brand identity system** in `brand/`. Completes the
  identity kit beyond the mark + basic docs:
  - **Paper + figure templates.** `brand/templates/dendra.mplstyle`
    for matplotlib Figure-1-consistent output (palette, type scale,
    color cycle with graphite primary + accent-orange secondary,
    muted tertiaries). `brand/templates/dendra-preamble.tex` for
    the paper — TeX Gyre Pagella body, Space Grotesk display,
    JetBrains Mono code, color definitions matching the palette.
  - **Voice + messaging + motion docs.** `brand/voice.md` (technical/
    measured/quiet tone; word-use lists; person/voice rules;
    primitive framing; error-message style). `brand/messaging.md`
    (canonical tagline, 15s/30s/120s pitches, audience-specific
    framings, positioning statement, core-claims table with
    citations). `brand/motion.md` (one canonical animation:
    rising-accent at 700 ms; reduced-motion handling; canonical
    easing curves; where not to animate).
  - **Animated mark.** `brand/logo/dendra-mark-animated.svg`
    (one-shot rising-accent for hero / phase-transition
    confirmation) and `dendra-mark-animated-loop.svg` (1500 ms
    loading cycle). Pure SVG+SMIL, no JS dependency.
  - **Sub-brand lockup system.** `brand/sub-brands.md` documents
    the typographic pattern (DENDRA parent + product-name child).
    Lockups shipped for DENDRA CLOUD, DENDRA ANALYZE, DENDRA
    INSIGHT, DENDRA RESEARCH.
  - **Applied assets.** GitHub repo social preview (1280×640),
    Twitter/X profile banner (1500×500), LinkedIn company banner
    (1128×191) — SVG masters + PNG exports.
  - **PWA / web-app manifest.** `brand/logo/site.webmanifest`
    referencing every favicon size with correct theme/background
    colors.
  - **Accessibility + governance docs.** `brand/accessibility.md`
    (contrast ratios per pairing, color-blindness behavior,
    alt-text conventions, small-size rules). `brand/governance.md`
    (who-can-change process, asset-addition workflow, trademark
    boundary, version history convention).
  - Preview page (`brand/logo/_preview.html`) extended to render
    every new asset alongside the existing kit.
- **Dendra brand kit** in `brand/`. The D2' · Node mark (rule
  floor parted by a rising accent, phase gate, hollow 28-r ring
  at the threshold-crossing point) is now the canonical Dendra
  identity. Selected through a three-round design process (21
  concept marks + 3 AI design-critic assessments; full record
  in `landing/assets/concepts/` and `notes/critic-assessments.md`).
  Inherits the B-Tree Labs palette + typography; adds Dendra-
  specific usage rules. Structure mirrors
  `b-tree-labs/.github/brand/` at the parent org.
  - `brand/logo/` — 12 SVG masters (mark / mark-color /
    mark-dark / mark-mono-light / mark-mono-dark /
    wordmark-horizontal{-dark} / wordmark-stacked{-dark} /
    favicon / social-card{-dark}) plus 14 PNG exports
    (favicon 16 / 32 / 180 / 512, mark 1024 × 4 variants,
    wordmark and social card at native size).
  - `brand/logo/_export.py` — regenerates PNGs from SVGs via
    cairosvg; idempotent.
  - `brand/logo/_preview.html` — renders every asset at typical
    use sizes for design review.
  - `brand/palette.md` / `brand/typography.md` / `brand/usage.md`
    — Dendra-specific brand docs.
- **Landing page integration of the Dendra mark.** Site header
  carries the mark alongside the DENDRA wordmark; favicon and
  apple-touch-icon are the real Dendra rounded-tile favicon;
  Open Graph / Twitter social card (1200×630 PNG) wired into the
  page's meta tags.
- **README.md branding.** Repository root README renders the
  Dendra horizontal wordmark at the top via GitHub's
  `<picture>` element, auto-switching between light and dark
  variants based on viewer color scheme.
- **Landing page scaffold** in `landing/`. Static single-page
  site built from the existing `landing-page-copy.md` deck,
  applying the B-Tree Labs brand system (palette, type, usage)
  from `b-tree-labs/.github/brand/`. No build step — drop
  the directory on Cloudflare Pages / Vercel / Netlify. Design
  patterns explicitly borrowed from Modal, Temporal, Clerk,
  Stripe, Honeycomb, Resend, Linear, Tailscale; anti-patterns
  (animations, dark-mode-only, "schedule a demo") explicitly
  avoided per `entry-with-end-in-mind.md` §4.
- **Example gallery** — 5 runnable, self-contained demos in
  `examples/` covering hello-world, outcome-log, safety-critical,
  LLM-shadow, and output-safety patterns. Each <100 lines, no
  external APIs.
- **`SUPPORT.md`** — routes users + contributors to the right
  channel (issues, security advisories, commercial licensing,
  trademark, design-partner program).
- **GitHub issue templates** (bug / feature / question) plus
  `.github/ISSUE_TEMPLATE/config.yml` routing security reports
  to the private advisory flow.
- **`docs/FAQ.md`** — pre-answers to the questions HN / r/ML
  commenters routinely ask (shadow mode / online learning /
  AutoML / licensing / patent / latency).
### Changed

- **License: now split Apache 2.0 + BSL 1.1.** The client SDK
  (decorator, config, storage, adapters, telemetry, viz,
  benchmarks) stays Apache 2.0 and is free for any use.
  Dendra-operated components (analyzer, ROI reporter, research /
  graduation tooling, CLI, future hosted surfaces) are now
  Business Source License 1.1 with Change Date **2030-05-01**
  (auto-converts to Apache 2.0) and an Additional Use Grant
  that permits customer production use but prohibits offering a
  competing hosted Dendra service.
- **`pyproject.toml` license metadata** moved to the PEP 639
  SPDX form: `license = "Apache-2.0 AND LicenseRef-BSL-1.1"`
  with an explicit `license-files` glob covering both license
  files plus the `LICENSE.md` split map.
- **`tests/test_telemetry_and_research.py`** split into
  `tests/test_telemetry.py` (Apache) and `tests/test_research.py`
  (BSL) so each test file matches the license of the module it
  exercises.

### Added

- **`LICENSE-APACHE`, `LICENSE-BSL`, `LICENSE.md`,
  `LICENSING.md`.** Split license text + the developer-facing
  "can I use this?" guide.
- **`TRADEMARKS.md`.** Repo-level trademark policy for DENDRA
  and B-TREE LABS. Describes descriptive /
  nominative fair use vs commercial uses that need a license.
- **`CODEOWNERS`.** Single-owner policy; IP, CI, and
  product-surface paths call out explicit ownership for future
  branch-protection enforcement.
- **DCO sign-off requirement** on contributions
  (`CONTRIBUTING.md`). Use `git commit -s`.
- **`.github/workflows/install-smoke.yml`.** Cross-OS install
  smoke test — builds a wheel, installs it into a fresh venv on
  ubuntu + macOS across Python 3.10 / 3.12 / 3.13, and
  verifies CLI + minimal switch decision round-trip.

### Documentation

- `LICENSE.md` expanded with the split-license rationale and
  the BSL Change Date mechanics.
- `LICENSING.md` added as the developer-facing "can I use this?"
  guide covering the Apache vs BSL boundary.
- `TRADEMARKS.md` added covering the DENDRA and B-TREE LABS marks.

## [1.0.0rc1] — 2026-05-02

Pre-launch release candidate. Captures the public-surface +
launch-infrastructure work from the 2026-05-01/02 launch sweep on
top of the v0.2.0 → v1.0.0 feature work that landed earlier.

### Added

- **Pricing primitive on the landing page.** ``landing/data/pricing-
  tiers.json`` is the single source of truth for tier data; the table
  + the new "find your tier · estimate your savings" calculator both
  render from JSON. ``landing/data/llm-prices.json`` is the single
  source of truth for provider rates and is auto-refreshed weekly
  from BerriAI/litellm's ``model_prices_and_context_window.json``
  via ``scripts/update_llm_prices.py`` + the
  ``refresh-llm-prices`` GitHub Action; the curated provider list
  covers the current 2026 frontier (Claude Opus 4.7 / Sonnet 4.6 /
  Haiku 4.5; GPT-5.5 / 5.4-mini; Gemini 3.1 Pro / 3 Flash; Grok
  4.20 reasoning; DeepSeek V3.2; Ollama local).
- **Paste-your-code Pyodide analyzer on the landing page.** ``#paste``
  section runs the bundled analyzer in WASM in the visitor's
  browser; pasted source never crosses the wire. Path to first
  personalized signal cut from ~5 minutes (visit → install → run)
  to ~10 seconds (visit → paste → results). Email/share capture
  via ``POST /v1/leads`` to the collector Worker — a new D1
  ``leads`` table (migration 0002) records email + result-shape
  signals without persisting source code. Per-origin CORS, no
  wildcard.
- **``priority_score`` + ``volume_estimate`` on every detected
  site.** The analyzer's old ``fit_score`` (graduation-fitness
  alone) is gone; replaced with ``priority_score`` — a composite
  of gate-fit × volume-factor × lift-factor (0–5) — and
  ``volume_estimate`` (cold/warm/hot derived from AST signals:
  route decorators → hot, cli.py / migrations / scripts → cold).
  ``dendra analyze`` gains ``--sort {priority,location,pattern,
  regime,lift}`` (default ``priority``) + ``--reverse``. Cohort-
  comparison line auto-emits in ``render_text`` when cohort_size
  ≥ 10 and the median field is populated; suppresses silently
  before that.
- **Dendra-on-Dendra dogfood (Risk-1 mitigation).** The analyzer's
  own pattern-dispatch and lift-status decisions are now
  ``@ml_switch``-wrapped at ``Phase.RULE``. Both surface in
  ``already_dendrified`` when the analyzer scans the dendra repo;
  hypothesis files committed at ``dendra/hypotheses/_classify_
  pattern.md`` + ``_classify_lift_status.md``.
- **Agent-harness integration examples.** Five self-contained
  examples in ``examples/integrations/`` covering LangChain
  (routing decision), LlamaIndex (retrieval-strategy selection),
  LiteLLM (classifier call site), NousResearch Hermes (tool-
  selection in agent loops), and Axiom OS (local-LM via ``axi
  serve``). Each runs offline with a deterministic stub fallback;
  parametrised contract test verifies the ``@ml_switch`` wrap +
  declared label set on each.
- **Report-card-as-evidence narrative across surfaces.** Three
  canonical sample report cards committed to
  ``docs/sample-reports/`` (per-switch graduation card, project
  summary rollup, initial-analysis discovery card) + the
  refused-and-recovering and pre-graduation samples. README +
  FAQ + landing ``#prove`` section all cite the new public
  samples and excerpt the same gate-evidence block (Phase:
  ML_PRIMARY, p = 4.2 × 10⁻⁴, +8.8 pp, $0.0042 → $0.000003 per
  call, 412 ms → 0.8 ms p50).
- **Cloudflare collector Worker + D1 events warehouse.** New
  ``cloud/collector/`` module: TypeScript Worker accepting
  ``POST /v1/events`` and ``POST /v1/leads``, payload-key
  whitelist enforced server-side, D1 batch insert for events,
  abuse-triage edge metadata stored on a 30-day TTL. Aggregator
  GitHub Action reads D1 nightly and republishes
  ``tuned-defaults.json`` for the cohort-defaults pull pipe.
- **Three-environment CI/CD.** New workflows: ``deploy-staging.yml``
  (push to main → tests → Pages + collector → smoke),
  ``deploy-production.yml`` (release tag → production with
  auto-rollback on smoke failure), ``aggregator.yml`` (03:00 UTC
  cohort aggregation), ``refresh-llm-prices.yml`` (Mondays 14:00
  UTC). Smoke tests at ``tests/smoke/`` exercise landing,
  collector, models CDN; bypass sandbox network guard via
  ``conftest.py`` autouse fixture.

### Changed

- **Closer #9 promoted to canonical primary tagline:** "Software
  that's smarter every month than the day you shipped it."
  Replaces "The classification primitive every production
  codebase is missing." across landing hero, README H1, ``<title>``,
  ``og:title``, ``twitter:title``, social-card alt text, and
  ``brand/messaging.md``. The displaced primary is documented as
  the "category descriptor" secondary tagline and survives in
  the landing footer + 2-minute pitch.
- **Performance baselines locked to a single source of truth.**
  ``docs/benchmarks/perf-baselines-2026-05-01.md`` is now the
  authoritative perf doc; README, FAQ, ``brand/messaging.md``,
  ``brand/voice.md``, and ``docs/limitations.md`` all cite it.
  Three previously-divergent claims (0.62 µs / 1.67 µs /
  0.50 µs) reconciled to current measurements: Phase.RULE
  classify 0.96 µs p50 / 1.04 µs p95; dispatch 1.00 µs p50 /
  1.08 µs p95; framework tax ~24× a bare Python call; FileStorage
  batched 245K writes/sec sustained.
- **Trademark posture softened to honesty before USPTO filing.**
  Landing footer's ``DENDRA™ and B-TREE LABS™ are trademarks
  of...`` claim corrected to ``DENDRA and B-TREE LABS are
  trademarks (or pending trademarks) of...`` — matches the
  wording already in LICENSING.md / SUPPORT.md / CONTRIBUTING.md
  / brand/usage.md. ™ symbols return after USPTO confirms
  registration.
- **Enterprise tiers reframed as design-partner pricing.** The
  four industry rows (Financial Services / Healthcare / SOC /
  Government) had compliance artifacts (FINRA, FDA SaMD, BAA,
  SOC 2 Type II, FedRAMP) and hard prices ($50K-$100K/yr) that
  a solo founder pre-launch can't deliver. Reframed: rows now
  read "Industry-tuned configuration; design-partner pricing"
  with "Custom" prices and "Apply to design-partner program"
  CTAs; design-partner explainer added under the table. The
  delivery work tracks in two roadmap entries (foundation
  prerequisites + industry-specific layers).
- **Domain rename completed.** Every reference to
  ``dendra.dev`` swept to ``dendra.run``; CDN at
  ``models.dendra.run`` provisioned with custom domain in front
  of R2.
- **DBA rename completed.** Every reference to ``Axiom Labs``
  swept to ``B-Tree Labs``; LaTeX preamble template path
  updated; CHANGELOG, brand docs, all surfaces consistent.

### Fixed

- ``X days ago`` time-relative phrases that go stale instantly in
  static documentation replaced with absolute ``YYYY-MM-DD``
  dates (README excerpt, landing prove section, sample card
  source). Saved as a feedback-memory rule so future writing
  doesn't repeat.
- Sample-card cross-references that pointed at non-public
  artifacts (``../hypotheses/<switch>.md``,
  ``archive/<switch>-<date>.md``) de-linked since those are
  per-deployment local artifacts, not part of the docs tree.
- Stale fit-score / Dendra-fit residue across landing JS, HTML,
  + 12 OSS-corpus JSON fixtures — all regenerated from current
  source via ``scripts/enrich_landing_corpus.py``.

### Added

- **Dendra Insights — opt-in cohort flywheel.** New
  ``dendra.insights`` package + CLI verbs ``dendra insights
  enroll`` / ``leave`` / ``status``. The OSS classification path
  remains telemetry-free by default; users explicitly opt in by
  running ``dendra insights enroll``, after which one anonymized
  shape-only event is queued per ``dendra analyze`` run and
  flushed best-effort on the next CLI invocation. What we capture:
  run-level histograms (pattern, regime, lift_status, hazard
  category) plus files_scanned / total_sites /
  already_dendrified_count. What we never capture: source code,
  function names, label values, prompt content, IP, machine ID,
  paths beyond a non-reversible repo-shape hash.

  The fetch side is on by default (no enrollment required):
  every install pulls
  ``https://dendra.run/insights/tuned-defaults.json`` once per
  day, caches at ``~/.dendra/tuned-defaults.json``, and falls
  back to baked-in defaults silently on any failure. The cohort
  defaults block carries cohort-size and timestamp so
  ``dendra insights status`` shows "tuned from N deployments as
  of Y." Receiving cohort wisdom does NOT require sharing data —
  asymmetry by design.

  Phase A (this release) ships the client-side wire +
  enrollment flow + analyze hook. Collector endpoint, nightly
  aggregator, EU residency, Stripe coupon wiring, and dashboard
  settings page are Phase B (v1.1, ~5 weeks post-launch).

### Changed

- **Company DBA renamed Axiom Labs → B-Tree Labs.** The "Axiom
  Labs" name conflicted with another company; the registered DBA
  under B-Tree Ventures, LLC is now **B-Tree Labs**. The GitHub
  org slug moves from `axiom-labs-os/dendra` to
  `b-tree-labs/dendra`; the social handle from `@axiom_labs` to
  `@btreelabs`. The separate Axiom product (the local-LM runtime
  formerly described as "Axiom node") is unaffected by the parent-
  company rename and continues as **Axiom OS** at
  `b-tree-labs/axiom-os`.
- **Trademark posture revised.** TRANSITION CURVES is removed
  from the trademark queue (descriptive mark in a crowded prior-
  art field; more valuable as freely-citable category vocabulary
  than as a registered mark). DENDRA remains the only P0 mark
  filed for B-Tree Labs (the company DBA) deferred to year 2.

### Fixed

- **Analyzer self-host correctness** (per the 2026-04-28 dogfood
  report). `dendra analyze` previously recommended re-graduating
  code that was already wrapped, recursed into its own generated
  companion modules, and double-counted sites through nested git
  worktrees. The full punch list:
  - Functions decorated with `@ml_switch` / `@dendra.ml_switch`
    are now flagged as `already_dendrified` and surfaced in a new
    `report.already_dendrified` field instead of being recommended
    for graduation. The same path applies in
    `analyze_function_source`, which now returns
    `LiftStatus.ALREADY_DENDRIFIED` for decorated functions.
  - Methods of classes that subclass `Switch` (or `dendra.Switch`)
    are skipped entirely — the analyzer cannot tell `_rule` apart
    from `_evidence_*` / `_when_*` / `_on_*` helpers without
    semantic understanding of the Switch contract.
  - `__dendra_generated__/` and `.claude/` are now in the default
    ignore list.
  - Directories containing a `.git` *file* (the marker for nested
    git worktrees) are skipped, eliminating the double-count from
    parallel worktrees.
  - Project self-blacklist: when scanning a directory whose
    `pyproject.toml` declares `name = "dendra"` (or
    `name = "dendra-*"`), `src/dendra/` is skipped so the
    analyzer's own infrastructure (analyzer/gates/models adapter
    plumbing) is not flagged.
- **Pattern P4 widened.** A new "argmax over a per-label scoring
  loop" sub-detector recovers `ReferenceRule.classify` and similar
  dict-driven keyword scanners whose returns are dynamic
  (`return best_label`) rather than literal-string. The original
  `if kw in text: return LABEL` shape is unchanged.

Net effect on a fresh self-scan of the repo: 113 sites → 62, with
35 already-dendrified surfaced and only the two genuine runtime-
wrapped rules in `examples/17` and `examples/18` remaining as
auto-liftable.

### Changed

- **`build_reference_rule` now shuffles the training stream by default**
  (`shuffle=True`, deterministic `shuffle_seed=0`) before slicing the
  seed window. The HuggingFace train splits for Banking77, HWU64,
  CLINC150, and Snips are sorted by label, so the previous behavior
  collapsed the auto-rule to a single class (predict-the-modal-class
  at chance accuracy). Under the new default the Banking77 rule jumps
  from 1.30 % to a median of ≈ 24 % across shuffle seeds, and Snips
  jumps from 14.3 % to ≈ 75 %. The `dendra bench` CLI gains `--no-shuffle` and
  `--shuffle-seed` flags. Migration: pass `shuffle=False` to
  `build_reference_rule` (or `--no-shuffle` to `dendra bench`) to
  reproduce the v0.x paper-as-shipped numbers.

## [0.2.0] — 2026-04-21

Initial public release. Six-phase graduated-autonomy classification
primitive with measured transition curves on four public benchmarks.

### Added

- **Six-phase lifecycle.** RULE → LLM_SHADOW → LLM_PRIMARY →
  ML_SHADOW → ML_WITH_FALLBACK → ML_PRIMARY with phase-specific
  routing and statistical transition gates.
- **Statistical transition gate** via McNemar's exact paired test
  (exact binomial for small samples; continuity-corrected normal
  approximation above 50 disagreements). Unpaired two-proportion
  z-test fallback when per-example data isn't available.
- **Safety-critical architectural cap.** `SwitchConfig(safety_critical=
  True)` refuses construction in `Phase.ML_PRIMARY`.
- **Circuit breaker** at Phase 5 with persistent-reverted state and
  explicit `reset_circuit_breaker()` recovery.
- **Shadow-path isolation.** Observational classifier failures
  (exceptions, timeouts, invalid outputs) cannot affect user-visible
  output.
- **Self-rotating outcome storage.** `FileStorage` with configurable
  segment size + retention count; bounded growth without cron.
- **LLM adapters.** OpenAI-compatible, Anthropic, Ollama, llamafile.
  Provider-agnostic protocol.
- **ML head protocol.** `SklearnTextHead` default (TF-IDF + LR);
  auto feature detection via `serialize_input_for_features`.
- **`dendra` CLI** with subcommands:
  - `dendra init FILE:FUNCTION --author @principal:context` — AST-
    based `@ml_switch` injector.
  - `dendra analyze PATH` — static-analysis scanner finding
    classification sites via 6 AST patterns; markdown/JSON output;
    optional savings projection.
  - `dendra bench {banking77, clinc150, hwu64, atis}` — reproduce the
    paper's transition-curve measurements.
  - `dendra plot` — Figure 1-style multi-panel transition curves.
  - `dendra roi` — self-measured ROI report from outcome logs.
- **Research instrumentation.** `run_transition_curve` +
  `run_benchmark_experiment` with shuffle-seed support.
- **LLM-as-teacher helper.** `train_ml_from_llm_outcomes` for
  bootstrapping an ML head from LLM-labeled production outcomes.
- **ROI reporter.** Exposes 8+ configurable assumptions
  (engineering cost, regression cost, token cost, time-to-ML
  acceleration); emits per-switch + portfolio summaries.
- **Output-safety pattern.** Paper §9.3 / Property 7 — the primitive
  wraps LLM output classification with the same safety-critical
  guarantees.
- **195 tests** across core, storage, decorator, LLM phases, ML
  phases, output safety, ROI, security, security benchmarks, wrap,
  viz, analyzer, research, telemetry, latency, benchmark loaders,
  and LLM-as-teacher.
- **Four public benchmarks** measured end-to-end: ATIS (26 labels),
  HWU64 (64), Banking77 (77), CLINC150 (151). Transition depths
  measured at `p < 0.01` paired.
- **Latency measurements.** Switch Phase-0 overhead 0.62 µs p50.
- **Security benchmarks.** 20-pattern jailbreak corpus, 25-item PII
  corpus, circuit-breaker stress test under 100 consecutive ML
  failures.
- **`.github/workflows/test.yml`** multi-Python CI.
- **`.github/workflows/release.yml`** PyPI trusted-publisher flow.
- **Claude Code SKILL.md** + **Cursor rules** integration.
- **GitHub Action template** (`docs/integrations/github-action-
  template.yml`) for drop-in PR-comment analyzer runs.

### Patent

- Filed US provisional patent application (pending). Apache-2.0 users
  receive a royalty-free patent license per the Apache grant.

## [0.1.0] — 2026-04-20

Initial scaffold (Phase 0 only). Not publicly released; superseded
by 0.2.0.
