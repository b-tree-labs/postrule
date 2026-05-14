# Postrule chaos test suite

Pre-launch failure-injection coverage for the v1 ship. Each test pins
ONE failure mode and asserts the contract Postrule's user-facing
docstrings already promise.

## Run

```bash
# Full suite (chaos runs by default)
.venv/bin/python -m pytest tests/

# Just the chaos suite
.venv/bin/python -m pytest tests/chaos/ -v

# Exclude chaos (when iterating fast on something else)
.venv/bin/python -m pytest tests/ -m "not chaos"

# Skip slow chaos (a few tests deliberately exercise a 100k-iter loop)
.venv/bin/python -m pytest tests/chaos/ -m "not slow"
```

## Categories

| File | Failure modes covered |
| ---- | --------------------- |
| `test_storage_chaos.py` | Disk-full (ENOSPC), permission-denied, fsync errors, partial writes during rotation, file-deleted-mid-write, oversized switch names, symlink loops, fd exhaustion, concurrent rotation, sqlite db deletion, BoundedInMemory edge inputs, dispatch survives storage failure. |
| `test_race_conditions.py` | Concurrent dispatch on the same switch, advance() racing dispatch, auto-advance during traffic, batched-flush vs sync-write races, lifter concurrency, RLock re-entrancy. |
| `test_shutdown_chaos.py` | KeyboardInterrupt mid-dispatch, SystemExit in handler, batched flush at close, idempotent close, append-after-close refusal, flusher join deadline, atexit durability. |
| `test_model_adapter_chaos.py` | Adapter raises Timeout / Conn-Reset / RuntimeError / HTTP 4xx/5xx, malformed adapter outputs (empty label, NaN confidence, out-of-range confidence), slow adapter, adapter constructor validation. |
| `test_memory_pressure.py` | 1 MB inputs, 1000-label switches, RSS drift across 100k dispatches, BoundedInMemoryStorage cap enforcement. |
| `test_clock_chaos.py` | Wall clock jumping backwards, monotonic-vs-wall divergence in `action_elapsed_ms`, TZ env-var change, far-future timestamps. |
| `test_lifter_chaos.py` | Many branches (recursion bug found), deeply-nested ifs, malformed Python (SyntaxError surfaces), unicode identifiers, many parameters, eval/exec/getattr refusal, recursive references, empty bodies, side-effects in branches, huge docstrings. |

## Test policy

- **Sandboxed by default.** Every chaos test inherits the global
  sandbox harness from `tests/conftest.py` (HOME redirect, network
  block, external-write block). No test opts out. The atexit subprocess
  test is the only exception; it spawns a child that points at the
  parent's `tmp_path` explicitly, so no escape.
- **One failure mode per test.** Don't fold "what about both" tests
  in here.
- **Bugs go in xfail, not silent-fix.** If a chaos test catches a
  legitimate bug, mark it `xfail(strict=False, reason="...")` and
  point at the triage classification (launch-blocker / v1.1 / won't-fix).
  Do not silently fix the underlying code unless the change is a
  one-line defensive nit. Anything bigger lands in its own PR.
- **`@pytest.mark.slow` for >5s tests.** The chaos marker is registered
  in `pyproject.toml` `[tool.pytest.ini_options].markers` and runs by
  default; `slow` is a separate axis combined with chaos for the
  long-running ones (atexit subprocess, RSS drift, 1000-branch lift).

## Bugs surfaced (xfail with triage)

| Bug | Severity | Test |
| --- | --- | --- |
| `lift_branches` recurses on `_copy_if(orelse[0])` for very long elif chains. ~995 branches busts Python's stack. | v1.1 hardening (rare in practice; one-line refusal could be added pre-launch) | `test_lifter_chaos.py::TestManyBranches::test_one_thousand_branches_lifts_or_refuses_in_time` |
| Adapter returning `ModelPrediction(label="", confidence>=threshold)` is accepted as the decision instead of falling back to the rule. | v1.1 hardening (real adapters set `matched=False → confidence=0.0` so this rarely surfaces) | `test_model_adapter_chaos.py::TestAdapterMalformedOutputs::test_empty_label_treated_as_no_decision` |

Tests that fail without an `xfail` marker are **active red bars** and
should block the merge. Don't suppress them.
