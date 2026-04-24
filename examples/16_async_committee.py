# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0
"""Async LLM committee — asyncio.gather across N judges in parallel.

Run: `python examples/16_async_committee.py`

When every judge is an independent network call, firing them
sequentially pays the sum of latencies. Firing them concurrently
pays the max. For a 3-judge committee at 300 ms per judge, that's
900 ms sequential vs ~300 ms concurrent — large enough to change
whether the committee fits inside a request budget.

:class:`LLMCommitteeSource.ajudge` uses ``asyncio.gather`` under
the hood: each judge's ``aclassify`` is scheduled on the same
event loop; aggregation happens once all N return. Judges that
expose only sync ``classify`` fall back to ``asyncio.to_thread``,
so mixed sync+async committees still work.
"""

from __future__ import annotations

import asyncio
import time

from dendra import ModelPrediction, Verdict
from dendra.verdicts import LLMCommitteeSource


class _AsyncStubJudge:
    """Stand-in for a real async LLM adapter. Fixed verdict, tunable delay."""

    def __init__(self, model: str, verdict: str, delay_ms: int) -> None:
        self._model = model
        self._verdict = verdict
        self._delay = delay_ms / 1000.0

    async def aclassify(self, input, labels):
        await asyncio.sleep(self._delay)
        return ModelPrediction(label=self._verdict, confidence=0.9)


def _build_committee() -> LLMCommitteeSource:
    return LLMCommitteeSource(
        [
            _AsyncStubJudge("gpt-4o-mini", "correct", delay_ms=300),
            _AsyncStubJudge("claude-haiku-4-5", "correct", delay_ms=300),
            _AsyncStubJudge("llama3.2:1b", "incorrect", delay_ms=300),
        ],
        mode="majority",
    )


def run_sync() -> float:
    """Pure sync context: judge() serializes asyncio.run() per judge."""
    committee = _build_committee()
    t0 = time.perf_counter()
    v = committee.judge({"title": "app crashes on login"}, "bug")
    elapsed = (time.perf_counter() - t0) * 1000
    print(f"judge  (sequential):  verdict={v.value}  took {elapsed:.0f}ms")
    return elapsed


async def run_async() -> float:
    """Async context: ajudge() uses asyncio.gather across judges."""
    committee = _build_committee()
    t0 = time.perf_counter()
    v = await committee.ajudge({"title": "app crashes on login"}, "bug")
    elapsed = (time.perf_counter() - t0) * 1000
    print(f"ajudge (parallel):    verdict={v.value}  took {elapsed:.0f}ms")
    return elapsed


def main() -> None:
    committee = _build_committee()
    print(f"committee: {committee.source_name}\n")
    sequential_ms = run_sync()
    parallel_ms = asyncio.run(run_async())
    speedup = sequential_ms / max(parallel_ms, 1.0)
    print(f"\nasync speedup: {speedup:.1f}x on a 3-judge committee")


if __name__ == "__main__":
    main()
