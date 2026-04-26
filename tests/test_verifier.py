# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""verifier= autonomous-verification default."""

from __future__ import annotations

import asyncio

import pytest

from dendra import (
    BoundedInMemoryStorage,
    CallableVerdictSource,
    JudgeSource,
    LearnedSwitch,
    ModelPrediction,
    NoVerifierAvailableError,
    Phase,
    SwitchConfig,
    Verdict,
    default_verifier,
)


def _rule(x):
    return f"rule-{x}"


class _StubLM:
    _model = "stub-1"

    def __init__(self, reply: str = "correct") -> None:
        self._reply = reply

    def classify(self, input, labels):
        return ModelPrediction(label=self._reply, confidence=0.95)


class _AsyncStubLM:
    _model = "async-stub-1"

    def __init__(self, reply: str = "correct") -> None:
        self._reply = reply

    async def aclassify(self, input, labels):
        await asyncio.sleep(0.001)
        return ModelPrediction(label=self._reply, confidence=0.95)


# ---------------------------------------------------------------------------
# Sync verifier on classify
# ---------------------------------------------------------------------------


class TestVerifierOnClassify:
    def test_verifier_records_verdict_directly(self):
        verifier = JudgeSource(_StubLM(reply="correct"))
        sw = LearnedSwitch(
            rule=_rule,
            name="v_correct",
            author="t",
            config=SwitchConfig(auto_advance=False, verifier=verifier),
            storage=BoundedInMemoryStorage(),
        )
        sw.classify("x")
        recs = sw.storage.load_records(sw.name)
        # One verdict-bearing record (CORRECT), no UNKNOWN auto-log.
        assert len(recs) == 1
        assert recs[0].outcome == Verdict.CORRECT.value
        assert recs[0].source.startswith("judge:")

    def test_no_verifier_keeps_auto_record_unknown(self):
        sw = LearnedSwitch(
            rule=_rule,
            name="v_none",
            author="t",
            config=SwitchConfig(auto_advance=False),
            storage=BoundedInMemoryStorage(),
        )
        sw.classify("x")
        recs = sw.storage.load_records(sw.name)
        # Without a verifier, the existing auto_record UNKNOWN
        # behavior holds.
        assert len(recs) == 1
        assert recs[0].outcome == Verdict.UNKNOWN.value

    def test_verifier_failure_falls_back_to_unknown(self):
        def boom(_input, _label):
            raise RuntimeError("verifier outage")

        verifier = CallableVerdictSource(boom, name="flaky")
        sw = LearnedSwitch(
            rule=_rule,
            name="v_fail",
            author="t",
            config=SwitchConfig(auto_advance=False, verifier=verifier),
            storage=BoundedInMemoryStorage(),
        )
        sw.classify("x")
        recs = sw.storage.load_records(sw.name)
        # Verifier failure must NOT break classify. Falls back to
        # the UNKNOWN auto-record so the observation isn't lost.
        assert len(recs) == 1
        assert recs[0].outcome == Verdict.UNKNOWN.value

    def test_verifier_incorrect_records_incorrect(self):
        verifier = JudgeSource(_StubLM(reply="incorrect"))
        sw = LearnedSwitch(
            rule=_rule,
            name="v_incorrect",
            author="t",
            config=SwitchConfig(auto_advance=False, verifier=verifier),
            storage=BoundedInMemoryStorage(),
        )
        sw.classify("x")
        rec = sw.storage.load_records(sw.name)[0]
        assert rec.outcome == Verdict.INCORRECT.value


# ---------------------------------------------------------------------------
# Sample rate
# ---------------------------------------------------------------------------


class TestVerifierSampleRate:
    def test_zero_sample_rate_skips_verifier(self):
        verifier = JudgeSource(_StubLM(reply="correct"))
        sw = LearnedSwitch(
            rule=_rule,
            name="v_zero",
            author="t",
            config=SwitchConfig(auto_advance=False, verifier=verifier, verifier_sample_rate=0.0),
            storage=BoundedInMemoryStorage(),
        )
        for i in range(20):
            sw.classify(i)
        recs = sw.storage.load_records(sw.name)
        # 0.0 sample rate: verifier never fires, all records are
        # the UNKNOWN auto-log.
        assert all(r.outcome == Verdict.UNKNOWN.value for r in recs)

    def test_invalid_sample_rate_rejected(self):
        with pytest.raises(ValueError, match="verifier_sample_rate"):
            SwitchConfig(verifier_sample_rate=1.5)
        with pytest.raises(ValueError, match="verifier_sample_rate"):
            SwitchConfig(verifier_sample_rate=-0.1)

    def test_partial_sample_rate_some_verified_some_unknown(self):
        """At a partial rate, mixed records appear."""
        import random

        random.seed(0)
        verifier = JudgeSource(_StubLM(reply="correct"))
        sw = LearnedSwitch(
            rule=_rule,
            name="v_partial",
            author="t",
            config=SwitchConfig(auto_advance=False, verifier=verifier, verifier_sample_rate=0.3),
            storage=BoundedInMemoryStorage(),
        )
        for i in range(100):
            sw.classify(i)
        recs = sw.storage.load_records(sw.name)
        verified = sum(1 for r in recs if r.outcome == Verdict.CORRECT.value)
        unknown = sum(1 for r in recs if r.outcome == Verdict.UNKNOWN.value)
        # Roughly 30% verified at sample_rate=0.3. Wide tolerance
        # for randomness; the point is "neither is 0% nor 100%."
        assert 10 <= verified <= 50
        assert 50 <= unknown <= 90


# ---------------------------------------------------------------------------
# Self-judgment guardrail at construction
# ---------------------------------------------------------------------------


class TestSelfJudgmentGuardrail:
    def test_same_model_as_model_and_verifier_refused(self):
        shared = _StubLM()
        with pytest.raises(ValueError, match="same language model"):
            LearnedSwitch(
                rule=_rule,
                name="v_same_llm",
                author="t",
                model=shared,
                config=SwitchConfig(
                    starting_phase=Phase.MODEL_SHADOW,
                    verifier=JudgeSource(shared),
                ),
            )

    def test_different_models_permitted(self):
        m = _StubLM()
        m._model = "model-A"
        v = _StubLM()
        v._model = "judge-B"
        sw = LearnedSwitch(
            rule=_rule,
            name="v_distinct",
            author="t",
            model=m,
            config=SwitchConfig(
                starting_phase=Phase.MODEL_SHADOW,
                verifier=JudgeSource(v),
            ),
        )
        assert sw.config.verifier is not None


# ---------------------------------------------------------------------------
# Async path — verifier runs natively via ajudge
# ---------------------------------------------------------------------------


class TestAsyncVerifier:
    def test_aclassify_runs_async_verifier(self):
        verifier = JudgeSource(_AsyncStubLM(reply="correct"))
        sw = LearnedSwitch(
            rule=_rule,
            name="v_async",
            author="t",
            config=SwitchConfig(auto_advance=False, verifier=verifier),
            storage=BoundedInMemoryStorage(),
        )

        async def _run():
            await sw.aclassify("x")

        asyncio.run(_run())
        recs = sw.storage.load_records(sw.name)
        assert len(recs) == 1
        assert recs[0].outcome == Verdict.CORRECT.value

    def test_aclassify_no_verifier_falls_back_to_to_thread(self):
        sw = LearnedSwitch(
            rule=_rule,
            name="v_async_none",
            author="t",
            config=SwitchConfig(auto_advance=False),
            storage=BoundedInMemoryStorage(),
        )

        async def _run():
            return await sw.aclassify("x")

        result = asyncio.run(_run())
        assert result.label == "rule-x"


# ---------------------------------------------------------------------------
# default_verifier() factory
# ---------------------------------------------------------------------------


class TestDefaultVerifier:
    def test_no_backend_raises_with_helpful_message(self, monkeypatch):
        # Force every detection path to fail.
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        # Point Ollama at an unreachable host.
        with pytest.raises(NoVerifierAvailableError) as exc_info:
            default_verifier(ollama_host="http://localhost:1")
        msg = str(exc_info.value)
        # Helpful message lists at least one recovery option.
        assert any(
            hint in msg.lower() for hint in ["ollama", "openai_api_key", "anthropic_api_key"]
        )

    def test_prefer_invalid_path_raises(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(NoVerifierAvailableError):
            default_verifier(prefer="openai")
