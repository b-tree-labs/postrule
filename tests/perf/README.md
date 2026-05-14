# Postrule v1 perf-regression suite

Catches latency, throughput, and memory regressions before users do.
Lives outside the default `pytest tests/` run so the regular suite
stays fast; opt in with `-m perf`.

## Run

```bash
# Run all perf tests against committed baselines.
pytest tests/perf/ -m perf -v

# First run on a new machine, or after an intentional perf change:
# rewrite the committed baselines.
pytest tests/perf/ -m perf --update-baselines

# Skip the heavy memory probe (~100k dispatches; several seconds).
pytest tests/perf/ -m "perf and not slow"
```

## How it works

Each test calls `perf_record(metric_name, stats)` after measuring
something. The harness:

1. Loads `tests/perf/baselines/<metric_name>.json` if it exists.
2. If missing, or if `--update-baselines` is passed: writes the
   current stats and passes.
3. Otherwise compares the current `median` to the baseline. If
   outside tolerance (default 20% slowdown), fails with
   `"<metric> regressed from X to Y (Z% slowdown). Run with
   --update-baselines if intentional."`

`@perf_test(tolerance=0.30)` overrides the default tolerance per
test. Hot-path micro-benchmarks (sub-microsecond ops) use 30% because
scheduler jitter dominates the signal at that resolution.

## Categories

| File | Covers |
|---|---|
| `test_hot_path.py` | `dispatch` / `classify` overhead, sync vs async, storage append |
| `test_cold_start.py` | `import postrule`, decorator-at-import, Switch subclass introspection |
| `test_throughput.py` | Storage ops/sec, concurrent FileStorage, async dispatch |
| `test_lifter.py` | `lift_evidence`, `lift_branches`, `analyze_function_source`, `postrule analyze` |
| `test_memory.py` | `tracemalloc` over 10k / 100k dispatches, FileStorage fd-leak probe |
| `test_harness_sensitivity.py` | Validates the regression-detection comparator itself |

## Methodology notes

- **Timing**: `time.perf_counter_ns()` for tight resolution. N
  warmup iterations, then M timed iterations.
- **Memory**: `tracemalloc` (more deterministic than RSS).
  Baseline is captured *after* warmup to exclude module-import alloc.
- **File descriptors**: `/dev/fd` (macOS) or `/proc/self/fd` (Linux).
  Skipped on Windows.
- **Throughput**: run the op for N seconds, divide count by elapsed.
- **Stdlib only**: no `pyperf` / `pytest-benchmark` / `psutil`. Less
  ceremony, fewer dependencies.

## Baselines

Committed under `baselines/` so regressions surface across machines
within tolerance. Hardware variance is real — baselines were taken
on Apple M-class silicon. CI runners may need their own baselines
or a relaxed tolerance; first-run auto-record handles the common
case automatically.

The first time a test runs without a baseline, the harness records
the current measurement and passes. No initial seeding required —
just commit the resulting JSON.

## Adding a perf test

```python
import pytest
from tests.perf.conftest import measure, perf_test

pytestmark = pytest.mark.perf


@perf_test(tolerance=0.20)
def test_my_thing(perf_record):
    stats = measure(lambda: do_thing(), n=5000, warmup=500)
    perf_record(
        "my_thing_latency",
        stats,
        target=10_000.0,  # 10µs ceiling for the histogram
    )
    assert stats["median"] < 10_000
```

For throughput tests, use `measure_throughput` and pass
`higher_is_better=True` to `perf_record`. For memory probes, use
`measure_memory`.

## Ship-check contract

Perf tests are NOT part of `bash scripts/ship-check.sh` by design —
they are too noisy for a hard pre-push gate. Run them locally before
significant releases or when investigating user-reported regressions.
