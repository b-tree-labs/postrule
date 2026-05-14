# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Cold-start perf tests — package import, decorator-at-import,
Switch subclass introspection.

Measures the cost a user pays on the first ``import postrule`` and
on every fresh process boot.
"""

from __future__ import annotations

import statistics
import subprocess
import sys
import time
from pathlib import Path
from textwrap import dedent

import pytest

from postrule import Switch, ml_switch  # noqa: F401  (verifies cold-start ok)
from tests.perf.conftest import perf_test  # noqa: TID252

pytestmark = pytest.mark.perf


# ---------------------------------------------------------------------------
# 1. Package import time — `python -c "import postrule"`.
# ---------------------------------------------------------------------------


@perf_test(tolerance=0.30)
def test_import_postrule_cold(perf_record):
    """Target: under 200ms (median of 5 fresh-interpreter runs).

    Spawns a fresh Python process for each sample so the import is
    truly cold — module cache is empty.
    """
    samples_ns: list[int] = []
    for _ in range(5):
        # ``time python -c "import postrule"`` would also work, but the
        # subprocess-internal timer is more reliable than wall clock
        # because it excludes interpreter startup variance.
        out = subprocess.run(
            [
                sys.executable,
                "-c",
                "import time; t=time.perf_counter_ns(); "
                "import postrule; "
                "print(time.perf_counter_ns() - t)",
            ],
            capture_output=True,
            check=True,
            text=True,
        )
        samples_ns.append(int(out.stdout.strip()))
    samples_ns.sort()
    median = float(statistics.median(samples_ns))
    p95 = float(samples_ns[int(len(samples_ns) * 0.95) - 1])
    perf_record(
        "import_postrule_cold",
        {
            "median": median,
            "p95": p95,
            "min": float(samples_ns[0]),
            "max": float(samples_ns[-1]),
            "n": float(len(samples_ns)),
        },
        target=200_000_000.0,  # 200ms in ns
    )
    assert median < 200_000_000, (
        f"import postrule median {median / 1e6:.1f}ms exceeds 200ms target."
    )


# ---------------------------------------------------------------------------
# 2. Decorator-at-import time — 100 @ml_switch decorated functions.
# ---------------------------------------------------------------------------


_DECORATOR_HEAVY_MODULE = "from postrule import ml_switch\n" + "".join(
    f'\n@ml_switch(labels=["a", "b"])\ndef fn_{i}(x):\n    return "a" if x else "b"\n'
    for i in range(100)
)

_BARE_MODULE = "".join(f'\ndef fn_{i}(x):\n    return "a" if x else "b"\n' for i in range(100))


@perf_test(tolerance=0.30)
def test_decorator_at_import_overhead(perf_record, tmp_path: Path):
    """Target: under 100ms incremental over a bare module of the same shape."""
    decorated_path = tmp_path / "perf_decorated.py"
    decorated_path.write_text(_DECORATOR_HEAVY_MODULE)
    bare_path = tmp_path / "perf_bare.py"
    bare_path.write_text(_BARE_MODULE)

    def _measure_import(path: Path) -> int:
        # Fresh interpreter per sample — module cache empty, ml_switch
        # cost paid every time.
        out = subprocess.run(
            [
                sys.executable,
                "-c",
                f"import sys, time; sys.path.insert(0, {str(path.parent)!r}); "
                "import postrule; "  # warm postrule so the delta is decorator-only
                "t=time.perf_counter_ns(); "
                f"__import__({path.stem!r}); "
                "print(time.perf_counter_ns() - t)",
            ],
            capture_output=True,
            check=True,
            text=True,
        )
        return int(out.stdout.strip())

    decorated_samples = sorted(_measure_import(decorated_path) for _ in range(3))
    bare_samples = sorted(_measure_import(bare_path) for _ in range(3))
    decorated_median = float(statistics.median(decorated_samples))
    bare_median = float(statistics.median(bare_samples))
    delta = decorated_median - bare_median
    perf_record(
        "decorator_at_import_overhead",
        {
            "median": delta,
            "p95": delta,  # no per-sample p95 here; the delta is the metric
            "decorated_median_ns": decorated_median,
            "bare_median_ns": bare_median,
            "n": float(len(decorated_samples)),
        },
        target=100_000_000.0,  # 100ms
    )
    assert delta < 100_000_000, (
        f"100 @ml_switch decorators added {delta / 1e6:.1f}ms; target 100ms."
    )


# ---------------------------------------------------------------------------
# 3. Switch subclass instantiation — __init_subclass__ introspection.
# ---------------------------------------------------------------------------


_SWITCH_TEMPLATE = dedent(
    """
    class GenSwitch{i}(Switch):
        def _evidence_x(self, text: str) -> str:
            return text

        def _rule(self, evidence) -> str:
            return 'a' if 'x' in evidence.x else 'b'
    """
)


@perf_test(tolerance=0.30)
def test_switch_subclass_creation_100(perf_record):
    """Target: under 200ms for 100 distinct subclasses.

    Forces ``__init_subclass__`` introspection per class.
    """
    samples_ns: list[int] = []
    for _ in range(3):
        t0 = time.perf_counter_ns()
        for i in range(100):
            ns = {"Switch": Switch}
            exec(_SWITCH_TEMPLATE.format(i=i), ns)  # noqa: S102 - synthetic perf probe
        samples_ns.append(time.perf_counter_ns() - t0)
    samples_ns.sort()
    median = float(statistics.median(samples_ns))
    perf_record(
        "switch_subclass_creation_100",
        {
            "median": median,
            "p95": float(samples_ns[-1]),
            "min": float(samples_ns[0]),
            "max": float(samples_ns[-1]),
            "n": 100.0,
        },
        target=200_000_000.0,  # 200ms
    )
    assert median < 200_000_000, (
        f"100 Switch subclasses took median {median / 1e6:.1f}ms; target 200ms."
    )
