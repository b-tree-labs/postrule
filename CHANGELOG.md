# Changelog

All notable changes to Dendra are documented in this file. Format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Version numbers follow [Semantic Versioning](https://semver.org).

## [Unreleased]

### Added

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
- **`docs/marketing/analyzer-dogfood-2026-04-22.md`** —
  launch-week blog post with `dendra analyze` results from
  Sentry, PostHog, HuggingFace Transformers, LangChain (+
  calibration contrast from Airbyte CDK and dbt-core).
- **`docs/marketing/launch-post-drafts.md`** — copy-paste-ready
  drafts for HN + r/ML + LinkedIn, plus launch-day cadence
  guidance.

### Changed

- **License: now split Apache 2.0 + BSL 1.1.** The client SDK
  (decorator, config, storage, adapters, telemetry, viz,
  benchmarks) stays Apache 2.0 and is free for any use.
  Dendra-operated components (analyzer, ROI reporter, research /
  graduation tooling, CLI, future hosted surfaces) are now
  Business Source License 1.1 with Change Date **2030-05-01**
  (auto-converts to Apache 2.0) and an Additional Use Grant
  that permits customer production use but prohibits offering a
  competing hosted Dendra service. Rationale:
  `docs/working/license-strategy.md`.
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
- **`TRADEMARKS.md`.** Repo-level trademark policy for DENDRA,
  TRANSITION CURVES, and AXIOM LABS. Describes descriptive /
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

- **`docs/working/license-strategy.md`.** Full decision record
  for the split-license posture — rationale, BSL vs SSPL/ELv2
  tradeoff, per-file mapping, risks, mitigations.
- **`docs/working/trademark-strategy.md`.** Trademark filing
  strategy — DENDRA (P0), TRANSITION CURVES (P1 post-launch),
  AXIOM LABS (P2); why generic synonyms stay SEO fuel, not
  trademarks.
- Updates to `docs/marketing/entry-with-end-in-mind.md` §4,
  `docs/working/patent-strategy.md` §9, and
  `docs/marketing/business-model-and-moat.md` §3.1 to reflect
  the split.

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
  receive a royalty-free patent license per the Apache grant. See
  `docs/working/patent-strategy.md` §9.

## [0.1.0] — 2026-04-20

Initial scaffold (Phase 0 only). Not publicly released; superseded
by 0.2.0.
