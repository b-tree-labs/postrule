# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Async API — aclassify / adispatch / arecord_verdict + async LLM judge
committee. Tests use stdlib ``asyncio.run()`` to avoid a pytest-asyncio
dev-dep. If CI adds pytest-asyncio later, these can be rewritten with
``@pytest.mark.asyncio`` for cleaner ergonomics."""

from __future__ import annotations

import asyncio

import pytest

from dendra import (
    BulkVerdict,
    LearnedSwitch,
    ModelPrediction,
    Phase,
    SwitchConfig,
    Verdict,
)
from dendra.verdicts import (
    CallableVerdictSource,
    LLMCommitteeSource,
    LLMJudgeSource,
)


def _rule(x):
    return f"rule-{x}"


class _StubSyncLLM:
    _model = "sync-stub"

    def classify(self, input, labels):
        return ModelPrediction(label="bug", confidence=0.95)


class _StubAsyncLLM:
    _model = "async-stub"

    async def aclassify(self, input, labels):
        # Simulate a network hop so concurrency is observable.
        await asyncio.sleep(0.01)
        return ModelPrediction(label="correct", confidence=0.9)


class _StubAsyncJudge:
    def __init__(self, model: str, verdict: str, delay: float = 0.05) -> None:
        self._model = model
        self._verdict = verdict
        self._delay = delay

    async def aclassify(self, input, labels):
        await asyncio.sleep(self._delay)
        return ModelPrediction(label=self._verdict, confidence=0.9)


# ---------------------------------------------------------------------------
# Core switch async methods
# ---------------------------------------------------------------------------


class TestAsyncSwitchMethods:
    def test_aclassify_returns_same_as_classify(self):
        sw = LearnedSwitch(
            rule=_rule, name="a_classify", author="t",
            config=SwitchConfig(auto_record=False, auto_advance=False),
        )

        async def _run():
            sync_r = sw.classify("hello")
            async_r = await sw.aclassify("hello")
            assert sync_r.label == async_r.label
            assert sync_r.source == async_r.source

        asyncio.run(_run())

    def test_adispatch_fires_action(self):
        calls = []

        def _action(x):
            calls.append(x)
            return "done"

        sw = LearnedSwitch(
            rule=_rule, name="a_dispatch", author="t",
            labels={"rule-x": _action},
            config=SwitchConfig(auto_record=False, auto_advance=False),
        )

        async def _run():
            r = await sw.adispatch("x")
            assert r.action_result == "done"
            assert calls == ["x"]

        asyncio.run(_run())

    def test_arecord_verdict_persists(self):
        sw = LearnedSwitch(
            rule=_rule, name="a_record", author="t",
            config=SwitchConfig(auto_record=False, auto_advance=False),
        )

        async def _run():
            await sw.arecord_verdict(
                input="x", label="rule-x", outcome=Verdict.CORRECT.value,
            )

        asyncio.run(_run())
        assert len(sw.storage.load_records(sw.name)) == 1

    def test_abulk_record_verdicts_summary(self):
        sw = LearnedSwitch(
            rule=_rule, name="a_bulk", author="t",
            config=SwitchConfig(auto_record=False, auto_advance=False),
        )
        batch = [
            BulkVerdict(input=i, label=f"rule-{i}", outcome=Verdict.CORRECT.value)
            for i in range(5)
        ]

        async def _run():
            return await sw.abulk_record_verdicts(batch)

        s = asyncio.run(_run())
        assert s.recorded == 5

    def test_concurrent_aclassify_calls(self):
        """The whole point of async — many in-flight calls on one loop."""
        sw = LearnedSwitch(
            rule=_rule, name="a_concurrent", author="t",
            config=SwitchConfig(auto_record=False, auto_advance=False),
        )

        async def _run():
            results = await asyncio.gather(
                *(sw.aclassify(i) for i in range(20)),
            )
            return results

        results = asyncio.run(_run())
        assert len(results) == 20
        assert {r.label for r in results} == {f"rule-{i}" for i in range(20)}


# ---------------------------------------------------------------------------
# Async LLMJudgeSource — ajudge uses aclassify when available
# ---------------------------------------------------------------------------


class TestAsyncLLMJudgeSource:
    def test_ajudge_uses_async_path_when_available(self):
        judge_model = _StubAsyncJudge("judge-async", "correct", delay=0.01)
        src = LLMJudgeSource(judge_model)

        async def _run():
            return await src.ajudge("x", "bug")

        assert asyncio.run(_run()) is Verdict.CORRECT

    def test_ajudge_falls_back_to_thread_for_sync_judge(self):
        """Mixed-mode: sync judge, async caller. Must still work."""
        src = LLMJudgeSource(_StubSyncLLM())

        async def _run():
            # _StubSyncLLM always says "bug" — not one of the
            # judge-label vocabulary, so this maps to UNKNOWN. The
            # important thing is that it doesn't hang or raise.
            return await src.ajudge("x", "bug")

        result = asyncio.run(_run())
        assert result in {Verdict.CORRECT, Verdict.INCORRECT, Verdict.UNKNOWN}


# ---------------------------------------------------------------------------
# Async LLMCommitteeSource — ajudge fires judges in parallel
# ---------------------------------------------------------------------------


class TestAsyncLLMCommitteeSource:
    def test_ajudge_runs_judges_in_parallel(self):
        """Committee latency should be ~max(delays), not sum(delays)."""
        import time as _time

        committee = LLMCommitteeSource(
            [
                _StubAsyncJudge("a", "correct", delay=0.1),
                _StubAsyncJudge("b", "correct", delay=0.1),
                _StubAsyncJudge("c", "correct", delay=0.1),
            ],
            mode="majority",
        )

        async def _run():
            t0 = _time.perf_counter()
            v = await committee.ajudge("x", "bug")
            return v, _time.perf_counter() - t0

        v, elapsed = asyncio.run(_run())
        assert v is Verdict.CORRECT
        # Sum of delays is 0.3s; parallel should finish in ~0.1s +
        # overhead. Give generous headroom for CI.
        assert elapsed < 0.25, (
            f"committee took {elapsed*1000:.0f}ms — looks serialized"
        )

    def test_ajudge_aggregates_majority(self):
        committee = LLMCommitteeSource(
            [
                _StubAsyncJudge("a", "correct", delay=0.01),
                _StubAsyncJudge("b", "correct", delay=0.01),
                _StubAsyncJudge("c", "incorrect", delay=0.01),
            ],
            mode="majority",
        )

        async def _run():
            return await committee.ajudge("x", "bug")

        assert asyncio.run(_run()) is Verdict.CORRECT


# ---------------------------------------------------------------------------
# abulk_record_verdicts_from_source — native-async path when source ajudge
# ---------------------------------------------------------------------------


class TestAsyncBulkFromSource:
    def test_async_pipeline_with_sync_source_uses_thread_wrap(self):
        """Sync VerdictSource still works via the async bulk path."""
        sw = LearnedSwitch(
            rule=_rule, name="abulk_sync", author="t",
            config=SwitchConfig(auto_record=False, auto_advance=False),
        )
        src = CallableVerdictSource(
            lambda i, l: Verdict.CORRECT, name="oracle",
        )

        async def _run():
            return await sw.abulk_record_verdicts_from_source(range(5), src)

        s = asyncio.run(_run())
        assert s.recorded == 5

    def test_async_pipeline_with_async_source_native_path(self):
        sw = LearnedSwitch(
            rule=_rule, name="abulk_async", author="t",
            config=SwitchConfig(auto_record=False, auto_advance=False),
        )

        class _AsyncOracle:
            source_name = "async-oracle"

            def judge(self, input, label):  # sync peer — unused in this test
                return Verdict.CORRECT

            async def ajudge(self, input, label):
                await asyncio.sleep(0.001)
                return Verdict.CORRECT

        async def _run():
            return await sw.abulk_record_verdicts_from_source(
                range(5), _AsyncOracle(),
            )

        s = asyncio.run(_run())
        assert s.recorded == 5
        recs = sw.storage.load_records(sw.name)
        assert all(r.source == "async-oracle" for r in recs)
