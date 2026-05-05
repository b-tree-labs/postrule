# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""``SwitchConfig.propagate_action_exceptions`` opt-in re-raise contract.

Today, when an ``on=`` callable raises during ``dispatch()``, ``_maybe_dispatch``
captures the exception, stringifies it onto ``result.action_raised``, and lets
``dispatch()`` return normally. That's the production-safe default: a misbehaving
handler never loses the classification decision; postmortem happens via
telemetry / the outcome log.

But pre-Dendra callers wrap their classify+act sites in ``try/except`` blocks
expecting handler exceptions to bubble. The ``propagate_action_exceptions`` knob
restores that parity opt-in. When set ``True``:

- ``action_raised`` and ``action_elapsed_ms`` are still recorded on the result.
- The auto-record / verifier / telemetry path still runs to completion (the
  storage row with ``action_raised`` populated lands BEFORE the re-raise).
- Then the original exception (with original type, message, and traceback) is
  re-raised so the caller's ``except`` block fires.

These tests are the load-bearing contract for that knob. ``KeyboardInterrupt``
and ``SystemExit`` propagate regardless of the knob (they always have).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from dendra import (
    BoundedInMemoryStorage,
    LearnedSwitch,
    MLPrediction,
    ModelPrediction,
    Phase,
    SwitchConfig,
)


class BillingDownError(Exception):
    """Custom exception so we can assert exact type propagates."""


# ---------------------------------------------------------------------------
# Stubs mirrored from tests/test_dispatch_across_phases.py so the cross-phase
# parametrization here uses identical adapter shapes.
# ---------------------------------------------------------------------------


@dataclass
class FailureContext:
    exception_type: str
    http_status: int | None = None
    endpoint: str = ""
    attempt: int = 1


def _rule(ctx: FailureContext) -> str:
    return "retry"


class StubModel:
    def __init__(self, label: str, confidence: float = 0.95) -> None:
        self._label = label
        self._confidence = confidence

    def classify(self, input, labels):  # noqa: A002
        return ModelPrediction(label=self._label, confidence=self._confidence)


class StubMLHead:
    def __init__(self, label: str, confidence: float = 0.99) -> None:
        self._label = label
        self._confidence = confidence

    def fit(self, records):
        return None

    def predict(self, input, labels):  # noqa: A002
        return MLPrediction(label=self._label, confidence=self._confidence)

    def model_version(self) -> str:
        return "stub-mlhead-1"


def _boom(ctx):
    raise BillingDownError("billing service is down")


def _ok(ctx):
    return f"ok:{ctx.endpoint}"


# ---------------------------------------------------------------------------
# Test 1 — default config preserves capture-not-propagate (current behavior).
# ---------------------------------------------------------------------------


class TestDefaultBehaviorPreserved:
    def test_default_config_captures_action_raised(self):
        sw = LearnedSwitch(
            rule=_rule,
            labels={"retry": _boom},
            author="propagate-tests",
            auto_advance=False,
            auto_record=False,
            name="propagate_default_capture",
        )
        ctx = FailureContext("HTTPError", http_status=503, endpoint="/api/v1")

        # Default: dispatch returns normally, no exception bubbles up.
        result = sw.dispatch(ctx)

        assert result.label == "retry"
        assert result.action_raised is not None
        assert "BillingDownError" in result.action_raised
        assert "billing service is down" in result.action_raised
        assert result.action_result is None
        assert result.action_elapsed_ms is not None
        assert result.action_elapsed_ms >= 0.0

    def test_default_config_propagate_attribute_is_false(self):
        cfg = SwitchConfig()
        assert cfg.propagate_action_exceptions is False


# ---------------------------------------------------------------------------
# Test 2 — propagate=True causes dispatch() to raise the original exception.
# ---------------------------------------------------------------------------


class TestPropagateTrueRaises:
    def test_propagate_true_raises_original_type(self):
        cfg = SwitchConfig(propagate_action_exceptions=True)
        sw = LearnedSwitch(
            rule=_rule,
            labels={"retry": _boom},
            author="propagate-tests",
            config=cfg,
            name="propagate_true_raises",
        )
        ctx = FailureContext("HTTPError", http_status=503, endpoint="/api/v1")

        with pytest.raises(BillingDownError, match="billing service is down"):
            sw.dispatch(ctx)

    def test_propagate_true_no_raise_when_handler_succeeds(self):
        """propagate=True only raises when the handler raises; success path stays normal."""
        cfg = SwitchConfig(propagate_action_exceptions=True)
        sw = LearnedSwitch(
            rule=_rule,
            labels={"retry": _ok},
            author="propagate-tests",
            config=cfg,
            name="propagate_true_no_raise_on_success",
        )
        ctx = FailureContext("HTTPError", http_status=503, endpoint="/api/v1")

        result = sw.dispatch(ctx)

        assert result.label == "retry"
        assert result.action_raised is None
        assert result.action_result == "ok:/api/v1"


# ---------------------------------------------------------------------------
# Test 3 — even when re-raising, the storage record lands first.
# ---------------------------------------------------------------------------


class TestStorageRecordLandsBeforeRaise:
    def test_action_raised_persisted_before_propagation(self):
        storage = BoundedInMemoryStorage()
        cfg = SwitchConfig(propagate_action_exceptions=True, auto_record=True)
        sw = LearnedSwitch(
            rule=_rule,
            labels={"retry": _boom},
            author="propagate-tests",
            config=cfg,
            storage=storage,
            name="propagate_storage_lands_first",
        )
        ctx = FailureContext("HTTPError", http_status=503, endpoint="/api/v1")

        with pytest.raises(BillingDownError):
            sw.dispatch(ctx)

        # Even though dispatch() re-raised, the auto-record row for this call
        # MUST have landed in storage with action_raised populated. Operators
        # need the failure visible in the outcome log even when the caller
        # opts into propagation.
        records = storage.load_records("propagate_storage_lands_first")
        assert len(records) == 1
        rec = records[0]
        assert rec.label == "retry"
        assert rec.action_raised is not None
        assert "BillingDownError" in rec.action_raised
        assert "billing service is down" in rec.action_raised
        assert rec.action_elapsed_ms is not None


# ---------------------------------------------------------------------------
# Test 4 — parametrize across RULE / MODEL_PRIMARY / ML_PRIMARY for both modes.
# ---------------------------------------------------------------------------


class TestParityAcrossPhases:
    @pytest.mark.parametrize(
        "phase",
        [Phase.RULE, Phase.MODEL_PRIMARY, Phase.ML_PRIMARY],
    )
    def test_default_captures_at_every_phase(self, phase):
        model = StubModel(label="retry", confidence=0.95)
        ml_head = StubMLHead(label="retry", confidence=0.99)
        sw = LearnedSwitch(
            rule=_rule,
            labels={"retry": _boom},
            author="propagate-tests",
            starting_phase=phase,
            model=model,
            ml_head=ml_head,
            auto_advance=False,
            auto_record=False,
            name=f"propagate_default_phase_{phase.value.lower()}",
        )
        ctx = FailureContext("HTTPError", http_status=503, endpoint="/api/v1")

        result = sw.dispatch(ctx)

        assert result.label == "retry"
        assert result.phase is phase
        assert result.action_raised is not None
        assert "BillingDownError" in result.action_raised

    @pytest.mark.parametrize(
        "phase",
        [Phase.RULE, Phase.MODEL_PRIMARY, Phase.ML_PRIMARY],
    )
    def test_propagate_true_raises_at_every_phase(self, phase):
        model = StubModel(label="retry", confidence=0.95)
        ml_head = StubMLHead(label="retry", confidence=0.99)
        cfg = SwitchConfig(
            propagate_action_exceptions=True,
            starting_phase=phase,
            auto_advance=False,
            auto_record=False,
        )
        sw = LearnedSwitch(
            rule=_rule,
            labels={"retry": _boom},
            author="propagate-tests",
            config=cfg,
            model=model,
            ml_head=ml_head,
            name=f"propagate_true_phase_{phase.value.lower()}",
        )
        ctx = FailureContext("HTTPError", http_status=503, endpoint="/api/v1")

        with pytest.raises(BillingDownError, match="billing service is down"):
            sw.dispatch(ctx)


# ---------------------------------------------------------------------------
# Test 5 — KeyboardInterrupt / SystemExit propagate regardless of knob.
# ---------------------------------------------------------------------------


class TestSystemExitsAlwaysPropagate:
    def test_keyboard_interrupt_propagates_with_default(self):
        def boom(ctx):
            raise KeyboardInterrupt()

        sw = LearnedSwitch(
            rule=_rule,
            labels={"retry": boom},
            author="propagate-tests",
            auto_advance=False,
            auto_record=False,
            name="propagate_default_kbint",
        )
        ctx = FailureContext("HTTPError")

        with pytest.raises(KeyboardInterrupt):
            sw.dispatch(ctx)

    def test_system_exit_propagates_with_default(self):
        def boom(ctx):
            raise SystemExit(1)

        sw = LearnedSwitch(
            rule=_rule,
            labels={"retry": boom},
            author="propagate-tests",
            auto_advance=False,
            auto_record=False,
            name="propagate_default_sysexit",
        )
        ctx = FailureContext("HTTPError")

        with pytest.raises(SystemExit):
            sw.dispatch(ctx)

    def test_keyboard_interrupt_propagates_with_propagate_true(self):
        def boom(ctx):
            raise KeyboardInterrupt()

        cfg = SwitchConfig(propagate_action_exceptions=True)
        sw = LearnedSwitch(
            rule=_rule,
            labels={"retry": boom},
            author="propagate-tests",
            config=cfg,
            name="propagate_true_kbint",
        )
        ctx = FailureContext("HTTPError")

        with pytest.raises(KeyboardInterrupt):
            sw.dispatch(ctx)


# ---------------------------------------------------------------------------
# Test 6 — async parity via adispatch.
# ---------------------------------------------------------------------------


class TestAsyncParity:
    def test_adispatch_default_captures(self):
        sw = LearnedSwitch(
            rule=_rule,
            labels={"retry": _boom},
            author="propagate-tests",
            auto_advance=False,
            auto_record=False,
            name="propagate_async_default",
        )
        ctx = FailureContext("HTTPError")

        result = asyncio.run(sw.adispatch(ctx))

        assert result.label == "retry"
        assert result.action_raised is not None
        assert "BillingDownError" in result.action_raised

    def test_adispatch_propagate_true_raises(self):
        cfg = SwitchConfig(propagate_action_exceptions=True)
        sw = LearnedSwitch(
            rule=_rule,
            labels={"retry": _boom},
            author="propagate-tests",
            config=cfg,
            name="propagate_async_true",
        )
        ctx = FailureContext("HTTPError")

        with pytest.raises(BillingDownError, match="billing service is down"):
            asyncio.run(sw.adispatch(ctx))

    def test_adispatch_propagate_true_storage_lands_first(self):
        storage = BoundedInMemoryStorage()
        cfg = SwitchConfig(propagate_action_exceptions=True, auto_record=True)
        sw = LearnedSwitch(
            rule=_rule,
            labels={"retry": _boom},
            author="propagate-tests",
            config=cfg,
            storage=storage,
            name="propagate_async_storage_lands_first",
        )
        ctx = FailureContext("HTTPError")

        with pytest.raises(BillingDownError):
            asyncio.run(sw.adispatch(ctx))

        records = storage.load_records("propagate_async_storage_lands_first")
        assert len(records) == 1
        assert records[0].action_raised is not None
        assert "BillingDownError" in records[0].action_raised
