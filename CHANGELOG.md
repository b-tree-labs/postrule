# Changelog

All notable changes to Dendra are documented in this file. Format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Version numbers follow [Semantic Versioning](https://semver.org).

## [Unreleased]

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
