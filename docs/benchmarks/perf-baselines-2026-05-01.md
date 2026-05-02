# Performance baselines — 2026-05-01

The authoritative numbers Dendra documents about itself. Every public
surface (README, FAQ, landing, brand/messaging.md) cites this file.

## How to reproduce

```bash
pip install -e ".[dev]"
python -m pytest tests/perf/ -m perf            # SDK micro-benchmarks
python -m pytest tests/test_latency.py -m ""    # end-to-end latency probes
```

The perf suite writes per-metric baselines to
`tests/perf/baselines/*.json`; running the suite updates them.
Re-render this doc after a meaningful regen.

## Measurement context

| | |
|---|---|
| Hardware | Apple M5 / 24 GB unified memory |
| OS | macOS 26 (Darwin 25.4) |
| Python | 3.13.12 (CPython, no-Free-Threading) |
| Build | release wheel from `pip install -e ".[dev]"` |
| Date | 2026-05-01 |

Numbers reflect framework overhead with stub model classifiers
(no I/O, no real ML). Real-LLM or real-ML latency is dominated by
the model's own inference time, not by Dendra.

## SDK hot-path latency

From `tests/perf/baselines/` (the structured perf suite). Per-call
overhead in Dendra's classify / dispatch path, model-stubbed so we
measure the framework itself.

| Operation | p50 | p95 | Notes |
|---|---:|---:|---|
| Raw Python call (baseline) | 42 ns | 83 ns | Reference floor — calling a function with no work. |
| `classify` at Phase.RULE | 0.96 µs | 1.04 µs | Returns the rule's label; no dispatch. |
| `dispatch` at Phase.RULE | 1.00 µs | 1.08 µs | Classify + invoke matched action; in-memory storage. |
| `dispatch` at Phase.MODEL_PRIMARY | 1.46 µs | 1.54 µs | Stubbed LM verifier; rule fallback unused. |
| `dispatch` at Phase.ML_PRIMARY | 1.50 µs | 1.58 µs | Stubbed ML head; rule fallback unused. |
| `adispatch` overhead vs sync | 222 µs | 250 µs | Async path; one event-loop spin-up per call. |

**Framework tax:** ~24× a bare Python call. In absolute terms ~1 µs
— fast enough that any production hot path is dominated by the
caller's own logic and (when phases beyond RULE engage) by the
model's inference, not by Dendra.

## End-to-end latency (with realistic model stubs)

From `tests/test_latency.py`. Uses a synthetic ML head that takes
~200 µs per `predict()` to mimic a realistic in-process classifier
(TF-IDF + LR class).

| Configuration | p50 | p95 | ops/sec |
|---|---:|---:|---:|
| Rule alone (bare Python) | 0.12 µs | 0.25 µs | 7.5M |
| ML head alone (synthetic) | 1.79 µs | 1.92 µs | 543K |
| Switch at Phase.RULE | 2.38 µs | 2.54 µs | 372K |
| Switch at Phase.ML_WITH_FALLBACK | 3.75 µs | 4.00 µs | 260K |

The Switch numbers include outcome-record creation; the perf-suite
numbers above measure dispatch with a different harness shape.
Both are valid framework-overhead probes; the difference (~1.4 µs)
is the per-call outcome-record allocation cost.

## Storage write throughput

From `tests/perf/baselines/throughput_*.json`. Steady-state write
rate sustained over a 0.5 s sample window.

| Backend | Throughput | Per-write |
|---|---:|---:|
| `BoundedInMemoryStorage` (default for ephemeral state) | 12M ops/sec | 0.08 µs |
| `FileStorage` with batching (production-recommended) | 245K ops/sec | 4.1 µs |
| `FileStorage` concurrent 4 threads (batched) | 181K ops/sec | 5.5 µs |
| `FileStorage` unbatched (per-call fsync; regulated workloads) | 28K ops/sec, 4 threads | 36 µs |
| Single `FileStorage` write (unbatched, p50) | — | 204 µs |
| `adispatch` at 100 coroutines | 77K dispatches/sec | — |

**Recommended for production:** `FileStorage` with batching.
Crash window ~50 ms; sustained 245K writes/sec/process; concurrent
4-thread regression test holds 181K writes/sec.

## Collector + cohort scale projections

The Cloudflare Worker that ingests cohort events
(`POST /v1/events`) and the new lead-capture endpoint
(`POST /v1/leads`) run on Cloudflare's edge runtime; capacity is
governed by Workers + D1 limits, not Worker code.

| Surface | Per-request budget | Effective ceiling |
|---|---|---|
| Worker CPU time | 50 ms (free) / 30 s (paid bundled) | n/a — well under both for batch insert |
| Workers free plan | 100K req/day | upgrade triggers automatically |
| Workers paid plan | unlimited | $0.30 per 1M requests |
| D1 batch insert (events) | up to 100 events per batch (client cap), atomic | ~1K events/sec sustained per batch endpoint |
| D1 single insert (leads) | 1 row per request | ~5K req/sec sustained |
| D1 read-row size | rare hot path; aggregator only | 1× nightly cron |

**Day-1 scale assumption.** Public launch + initial paid-tier
cohort: ~10 deployments enrolled, each emitting 1 `analyze` event
per `dendra analyze` invocation (~1 per dev session per day) plus
sparse `init_attempt` / `bench_phase_advance` events. Total ~100
events/day across the cohort. Effective load on the Worker is
microscopic.

**v1.1 scale headroom.** At 1,000 enrolled deployments × 10 events/
day = 10K events/day, still well under any Workers tier. D1 row
count after 90 days: ~900K rows; plenty of room before considering
table partitioning.

**Lead-capture (paste-analyzer flow).** A single visitor producing
a lead is roughly free. Even at 1K leads/day during launch week
(implausibly high), the endpoint is ≪ 1% of D1 capacity.

## What's *not* measured here (yet)

Tracked for v1.1 follow-on benchmarks; intentionally absent from
this doc until measured:

- **Real ML head latency on production traffic.** Synthetic ~200 µs
  stub stands in here; real `MLHead.predict()` for the four shipped
  ML adapters (TF-IDF + LR, sklearn pipeline, a transformer head,
  the cohort-tuned bundle) need their own per-classifier measurement.
- **Real local-SLM verifier latency.** `qwen2.5:7b` via Ollama
  measured at ~481 ms p50 in
  [`docs/benchmarks/slm-verifier-results.md`](slm-verifier-results.md);
  needs re-measurement on launch hardware.
- **Cold-start cost of `pip install dendra` + first-call.** Cohort
  traffic is the wedge — measure post-launch with real installs.
- **Multi-process FileStorage contention.** The current 4-thread
  test stays in a single process; the inter-process flock contention
  ceiling needs a multi-process probe.
- **CDN cache hit rate on `models.dendra.run`** for bundled-model
  downloads. Worth measuring after first 100 downloads.

Each of these resolves into a perf-suite test and a paragraph in
this doc as it lands.

---

*Update this file whenever any of the perf-suite baselines move
beyond their tolerance band, or when a new measurement context
(machine swap, Python version bump, OS upgrade) lands. The README
+ FAQ + brand/messaging.md cite this doc as the source of truth.*
