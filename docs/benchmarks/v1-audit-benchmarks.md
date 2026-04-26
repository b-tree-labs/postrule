# Benchmark refresh — v1 baseline

Run date: **2026-04-24**  
Measured by: `scripts/run_v1_benchmarks.py` (perf_counter_ns, 10k iter / cell, 1k warmup)

## Environment

- Python: 3.13.12 (main, Feb  3 2026, 17:53:27) [Clang 17.0.0 (clang-1700.6.3.2)]
- Implementation: CPython
- Platform: macOS-26.4.1-arm64-arm-64bit-Mach-O
- Machine: arm64
- CPU: Apple M5
- RAM: 24 GB
- Dendra: 0.2.0

## Hot path: `classify()` — phase × auto_record matrix

Storage: `BoundedInMemoryStorage` (default) for all rows.

| Mode | p50 | p95 | p99 | ops/sec |
|---|---:|---:|---:|---:|
| Phase.RULE, auto_record=False | 0.50 µs | 0.54 µs | 0.67 µs | 1.9M |
| Phase.RULE, auto_record=True **(default)** | 1.67 µs | 1.88 µs | 2.42 µs | 573k |
| Phase.MODEL_SHADOW, auto_record=False | 0.83 µs | 0.88 µs | 0.92 µs | 1.2M |
| Phase.MODEL_SHADOW, auto_record=True | 2.00 µs | 2.17 µs | 2.54 µs | 483k |
| Phase.MODEL_PRIMARY, auto_record=False | 0.83 µs | 0.92 µs | 1.00 µs | 1.2M |
| Phase.MODEL_PRIMARY, auto_record=True | 2.12 µs | 2.29 µs | 2.46 µs | 451k |
| Phase.ML_SHADOW, auto_record=False | 1.17 µs | 1.25 µs | 1.38 µs | 838k |
| Phase.ML_SHADOW, auto_record=True | 2.42 µs | 2.67 µs | 2.75 µs | 399k |
| Phase.ML_WITH_FALLBACK, auto_record=False | 0.92 µs | 0.96 µs | 1.00 µs | 1.1M |
| Phase.ML_WITH_FALLBACK, auto_record=True | 2.08 µs | 2.25 µs | 2.33 µs | 457k |
| Phase.ML_PRIMARY, auto_record=False | 0.88 µs | 0.96 µs | 1.04 µs | 1.1M |
| Phase.ML_PRIMARY, auto_record=True | 2.17 µs | 2.29 µs | 2.38 µs | 451k |

**auto_record tax at Phase.RULE (default-on):** +1.17 µs p50, **3.3×** bare classify cost. Every `classify()` call appends a ClassificationRecord (UNKNOWN outcome) to storage — the new default makes the hot path do real work.

## Hot path × storage backend (Phase.RULE, auto_record=True)

| Storage backend | p50 | p95 | p99 | ops/sec |
|---|---:|---:|---:|---:|
| BoundedInMemoryStorage | 1.71 µs | 1.83 µs | 2.25 µs | 561k |
| InMemoryStorage | 1.67 µs | 1.88 µs | 2.79 µs | 542k |
| FileStorage | 2.62 ms | 3.48 ms | 4.48 ms | 380 |
| SqliteStorage | 1.06 ms | 1.31 ms | 1.80 ms | 927 |

The durable backends (`FileStorage`, `SqliteStorage`) turn `classify()` into a disk-write on every call when `auto_record=True` — this flips the hot path from sub-microsecond to **~1 ms**. Users who don't need the auto-log should pass `auto_record=False`.

## `record_verdict()` × storage (auto_advance=False)

| Storage backend | p50 | p95 | p99 | ops/sec |
|---|---:|---:|---:|---:|
| BoundedInMemoryStorage | 1.71 µs | 1.83 µs | 1.96 µs | 560k |
| InMemoryStorage | 1.71 µs | 1.79 µs | 1.88 µs | 545k |
| FileStorage.fsync=False | 1.89 ms | 3.10 ms | 3.73 ms | 499 |
| FileStorage.fsync=True | 1.92 ms | 3.12 ms | 4.02 ms | 496 |
| SqliteStorage.sync=NORMAL | 1.05 ms | 1.31 ms | 1.89 ms | 927 |

## `record_verdict()` with `auto_advance=True`

`auto_advance_interval=100` means every 100th call triggers `advance()` (which walks the log + runs the gate). The spike shows up at the p99 — 99% of calls are normal, the 1% that pay for gate evaluation are slower.

| Config | p50 | p95 | p99 | ops/sec |
|---|---:|---:|---:|---:|
| interval=100 | 1.75 µs | 1.96 µs | 287.2 µs | 58k |

## `advance()` cost × log size

Gate: default `McNemarGate`. Records seeded with alternating correct/incorrect outcomes so paired-correctness math has real discordant pairs.

| Log size | p50 | p95 | p99 | ops/sec |
|---|---:|---:|---:|---:|
| 0 | 1.12 µs | 1.21 µs | 1.38 µs | 878k |
| 1,000 | 222.4 µs | 238.6 µs | 255.4 µs | 4k |
| 10,000 | 2.20 ms | 2.29 ms | 2.39 ms | 454 |
| 100,000 | 22.40 ms | 22.89 ms | 43.41 ms | 44 |

advance() is **O(n)** in log size. 1,000 → 100,000 records (100×) ≈ 100.7× time — linear-scan cost dominated by McNemar's single pass over the log + two accuracy sums.

## Gate evaluation cost (10k paired records)

| Gate | p50 | p95 | p99 | ops/sec |
|---|---:|---:|---:|---:|
| McNemarGate | 2.24 ms | 2.34 ms | 2.42 ms | 446 |
| AccuracyMarginGate | 2.07 ms | 2.17 ms | 2.27 ms | 482 |
| MinVolumeGate(McNemar) | 2.23 ms | 2.35 ms | 2.41 ms | 447 |
| CompositeGate.all_of[Mc,Acc] | 4.30 ms | 4.45 ms | 4.50 ms | 232 |

## Action-dispatch overhead

Same switch, same inputs, same (no-op) actions attached to every label. `classify()` ignores the actions; `dispatch()` fires the matched one.

| Entry | p50 | p95 | p99 | ops/sec |
|---|---:|---:|---:|---:|
| classify.RULE.with_labeled_actions | 0.54 µs | 0.58 µs | 0.67 µs | 1.9M |
| dispatch.RULE.with_labeled_actions | 1.04 µs | 1.12 µs | 1.29 µs | 949k |

Dispatch overhead over classify: **0.50 µs** p50 (label lookup + action invocation + action timing).

## Entry-point comparison (decorator)

| Entry | p50 | p95 | p99 | ops/sec |
|---|---:|---:|---:|---:|
| decorator.__call__ | 0.17 µs | 0.17 µs | 0.21 µs | 6.8M |
| decorator.classify | 0.54 µs | 0.58 µs | 0.71 µs | 1.8M |
| decorator.dispatch | 0.67 µs | 0.71 µs | 0.79 µs | 1.5M |

## Payload-size sweep for `record_verdict()`

| Storage | Payload | p50 | p95 | p99 | ops/sec |
|---|---:|---:|---:|---:|---:|
| BoundedInMemoryStorage | 100 B | 1.79 µs | 1.88 µs | 2.17 µs | 533k |
| FileStorage | 100 B | 2.26 ms | 3.01 ms | 3.70 ms | 446 |
| SqliteStorage | 100 B | 1.05 ms | 1.30 ms | 1.66 ms | 929 |
| BoundedInMemoryStorage | 1,024 B | 1.83 µs | 2.00 µs | 2.12 µs | 486k |
| FileStorage | 1,024 B | 2.28 ms | 3.03 ms | 3.76 ms | 456 |
| SqliteStorage | 1,024 B | 1.08 ms | 1.39 ms | 2.37 ms | 894 |
| BoundedInMemoryStorage | 10,240 B | 1.75 µs | 1.96 µs | 2.00 µs | 550k |
| FileStorage | 10,240 B | 2.13 ms | 2.93 ms | 3.49 ms | 482 |
| SqliteStorage | 10,240 B | 1.74 ms | 2.63 ms | 3.17 ms | 588 |
| BoundedInMemoryStorage | 102,400 B | 1.96 µs | 2.08 µs | 2.12 µs | 498k |
| FileStorage | 102,400 B | 3.63 ms | 5.14 ms | 6.04 ms | 288 |
| SqliteStorage | 102,400 B | 2.25 ms | 3.18 ms | 3.82 ms | 457 |

## Comparison to README claims

| README claim | Prior audit (2026-04-24) | New measurement | Delta |
|---|---:|---:|---|
| Bare rule call: `0.12 µs p50` | 0.17 µs (stale) | not re-measured (not on switch path) | stale; still an order-of-magnitude claim |
| Phase-0 classify: `0.62 µs p50` (5× rule) | 1.08 µs (prev audit) | **0.50 µs** (auto_record=False) | **README is stale** — retract the 0.62 µs figure. New claim: 0.50 µs p50 at Phase.RULE with auto_record=False. |
| Phase-0 classify *(default config)* | n/a | **1.67 µs** (auto_record=True) | New regression: default classify now writes an UNKNOWN record, ~3.3× cost. |
| TF-IDF ML head: `105 µs p50` | not measured | **not re-measured** (stub head used here) | Same unverified claim; needs a real trained head benchmark. |
| Ollama LLM: `~250 ms p50` | hardcoded constant | **not re-measured** (stub LLM used here) | Same unverified claim; run a live Ollama test on release hardware. |

## Updated numbers to pin in README + tests

- **Phase.RULE classify (auto_record=False):** 0.50 µs p50 / 0.54 µs p95 / 0.67 µs p99 (1.9M ops/sec)
- **Phase.RULE classify (auto_record=True, default):** 1.67 µs p50 / 1.88 µs p95 / 2.42 µs p99 (573k ops/sec)
- **Phase.MODEL_SHADOW classify (auto_record=False):** 0.83 µs p50 / 0.88 µs p95 / 0.92 µs p99 (1.2M ops/sec)
- **Phase.ML_WITH_FALLBACK classify (auto_record=False):** 0.92 µs p50 / 0.96 µs p95 / 1.00 µs p99 (1.1M ops/sec)
- **record_verdict (BoundedInMemory):** 1.71 µs p50 / 1.83 µs p95 / 1.96 µs p99 (560k ops/sec)
- **record_verdict (FileStorage, fsync=False):** 1.89 ms p50 / 3.10 ms p95 / 3.73 ms p99 (499 ops/sec)
- **record_verdict (FileStorage, fsync=True):** 1.92 ms p50 / 3.12 ms p95 / 4.02 ms p99 (496 ops/sec)
- **record_verdict (SqliteStorage, sync=NORMAL):** 1.05 ms p50 / 1.31 ms p95 / 1.89 ms p99 (927 ops/sec)
- **advance() at 10k log:** 2.20 ms p50 / 2.29 ms p95 / 2.39 ms p99 (454 ops/sec)
- **advance() at 100k log:** 22.40 ms p50 / 22.89 ms p95 / 43.41 ms p99 (44 ops/sec)
- **McNemarGate on 10k records:** 2.24 ms p50 / 2.34 ms p95 / 2.42 ms p99 (446 ops/sec)
- **dispatch() (no-op action):** 1.04 µs p50 / 1.12 µs p95 / 1.29 µs p99 (949k ops/sec)

## Regressions introduced by recent features

1. **`auto_record=True` (default) tax on classify:** 0.50 µs → 1.67 µs p50 (3.3×). Root cause: every classify now allocates a `ClassificationRecord` and calls `storage.append_record`. On the default `BoundedInMemoryStorage` this is cheap but non-zero; on `FileStorage`/`SqliteStorage` it becomes a sub-millisecond-to-millisecond write.
2. **`auto_advance_interval=100` spike:** every 100th record_verdict triggers `advance()`, which walks the full log. p50 is 1.75 µs (close to bare record_verdict at 1.71 µs), **p99 is 287.2 µs** — roughly 168× the p50. High-throughput verdict recorders should either disable auto_advance (`auto_advance=False`) or use a larger interval.
3. **Default `auto_record=True` + `persist=True` (FileStorage) is the worst-case cell:** 1.71 µs → 2.62 ms p50 (1537×). Every `classify()` becomes a fsync-free file append. Either document the pairing as a pro-mode trade-off or flip `auto_record` default off for `persist=True` paths.
4. **CompositeGate walks the log once per sub-gate.** McNemarGate alone: 2.24 ms. CompositeGate.all_of([Mc, Acc]): 4.30 ms (1.9× McNemar alone). Future optimization: share the paired-correctness extraction pass across sub-gates.

## Recommended next steps

1. **Retract the 0.62 µs README claim.** The `classify()` at Phase.RULE number is ~1 µs at best (auto_record=False); the default config is slower. Pin real p50/p99 numbers into `tests/test_latency_pinned.py` and let them fail CI on drift.
2. **Wire `tests/test_latency_pinned.py` into CI** with a `pytest -m benchmark` job that runs the new test on a dedicated runner (variance is lower on GH's `macos-14` or `ubuntu-latest` if kept to single-job). Acceptable drift: 2×.
3. **Document the `auto_record` default tax in the README.** Pair it with a recipe: `auto_record=False` for throughput-sensitive call sites.
4. **Record a live TF-IDF + real LLM measurement** to replace the hardcoded `105 µs` / `250 ms` README numbers. Current numbers above use stubs for determinism — they verify switch overhead, not ML-head cost.
5. **Investigate the `record_verdict` + `FileStorage` ~1 ms cost.** Keep-fd-open or buffered-append variant would drop this into tens of µs. Already flagged in the earlier perf audit — now that we ship `auto_record=True` by default, the remediation is higher priority.
6. **Re-run this suite on release hardware** before v1 and diff the JSONL against `docs/working/benchmarks/v1-baseline-YYYY-MM-DD.jsonl` — the raw data is stored one-row-per-cell so `jq` and pandas diffs are trivial.

## Methodology notes

- Measurements use `time.perf_counter_ns()` in a tight Python loop; 1000-iteration warm-up, 10,000-iteration measurement per cell (advance/gate cells scale down because a single call is 1–50 ms).
- Percentiles are per-call; `ops/sec` is derived from the mean.
- Background processes on the dev laptop introduce ~5–15% variance on any single cell. p99 is the noisiest bucket. Treat order-of-magnitude comparisons as load-bearing; single-digit-percent comparisons are inside the noise floor.
- Storage backends write to a scratch directory under `$TMPDIR` (local SSD). Numbers will be higher on spinning disk, lower on NVMe with fsync barriers relaxed.
