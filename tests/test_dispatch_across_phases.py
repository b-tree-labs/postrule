# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Dispatch fires ``on=`` callables identically across every Dendra phase.

The dispatch path (`core.dispatch` -> `_classify_impl` -> `_maybe_dispatch`)
is phase-blind: ``_maybe_dispatch`` looks up the chosen label on the switch's
label table and fires its ``on=`` callable regardless of which phase produced
the label. These tests document that contract empirically across RULE,
MODEL_SHADOW, MODEL_PRIMARY, ML_SHADOW, ML_WITH_FALLBACK, and ML_PRIMARY,
using the realistic exception-handling classifier from
``examples/17_exception_handling.py`` so the assertions aren't toy strings.

Where these tests fail, the failure is the bug.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from dendra import (
    LearnedSwitch,
    MLPrediction,
    ModelPrediction,
    Phase,
)

# ---------------------------------------------------------------------------
# Realistic running classifier — lifted shape from examples/17_exception_handling.py.
# ---------------------------------------------------------------------------


@dataclass
class FailureContext:
    """Everything a classifier sees about a failure."""

    exception_type: str
    http_status: int | None = None
    endpoint: str = ""
    attempt: int = 1
    elapsed_ms: float = 0.0


STRATEGIES = ["retry", "fallback", "escalate", "drop"]


def handling_rule(ctx: FailureContext) -> str:
    """Day-0 conservative dispatch on exception type + HTTP status."""
    if ctx.http_status in (502, 503, 504) and ctx.attempt < 3:
        return "retry"
    if ctx.http_status in (401, 403):
        return "escalate"
    if ctx.exception_type == "ValueError":
        return "drop"
    if ctx.exception_type == "TimeoutError":
        return "fallback"
    if ctx.exception_type in ("RuntimeError", "KeyError", "AttributeError"):
        return "escalate"
    return "retry" if ctx.attempt < 2 else "escalate"


# ---------------------------------------------------------------------------
# Stubs — match the real adapter shapes (ModelClassifier / MLHead).
# ---------------------------------------------------------------------------


class StubModel:
    """Model adapter that returns a configurable (label, confidence).

    Matches the :class:`dendra.models.ModelClassifier` protocol shape:
    ``classify(input, labels) -> ModelPrediction``.
    """

    def __init__(self, label: str, confidence: float = 0.95) -> None:
        self._label = label
        self._confidence = confidence
        self.calls: list[tuple[object, list[str]]] = []

    def classify(self, input, labels):  # noqa: A002 - mirrors protocol
        self.calls.append((input, list(labels)))
        return ModelPrediction(label=self._label, confidence=self._confidence)


class StubMLHead:
    """ML head returning configurable (label, confidence); can simulate failure.

    Matches the :class:`dendra.ml.MLHead` protocol shape:
    ``predict(input, labels) -> MLPrediction``, with ``fit`` / ``model_version``.
    """

    def __init__(
        self,
        label: str | None = None,
        confidence: float = 0.99,
        fail: bool = False,
    ) -> None:
        self._label = label
        self._confidence = confidence
        self._fail = fail
        self.calls: list[tuple[object, list[str]]] = []

    def fit(self, records):  # protocol method (unused in these tests)
        return None

    def predict(self, input, labels):  # noqa: A002 - mirrors protocol
        self.calls.append((input, list(labels)))
        if self._fail:
            raise RuntimeError("stub ML head failure")
        return MLPrediction(label=self._label or "", confidence=self._confidence)

    def model_version(self) -> str:
        return "stub-mlhead-1"


class BillingDownError(Exception):
    """Custom exception used to verify dispatch captures the type name."""


# ---------------------------------------------------------------------------
# Handler factory — each test gets a fresh quartet so we can inspect them.
# ---------------------------------------------------------------------------


def _build_handlers():
    fired: dict[str, list] = {k: [] for k in STRATEGIES}

    def do_retry(ctx):
        fired["retry"].append(ctx)
        return f"requeued {ctx.endpoint} (attempt {ctx.attempt + 1})"

    def do_fallback(ctx):
        fired["fallback"].append(ctx)
        return f"served cached response for {ctx.endpoint}"

    def do_escalate(ctx):
        fired["escalate"].append(ctx)
        return f"pushed {ctx.endpoint} + {ctx.exception_type} to ops queue"

    def do_drop(ctx):
        fired["drop"].append(ctx)
        return f"logged {ctx.exception_type} and continued"

    return fired, {
        "retry": do_retry,
        "fallback": do_fallback,
        "escalate": do_escalate,
        "drop": do_drop,
    }


def _make_switch(*, phase, model=None, ml_head=None, labels=None, **kwargs):
    """LearnedSwitch factory honoring the constraints in the spec.

    ``BoundedInMemoryStorage`` (default), the realistic ``handling_rule``,
    and a unique ``name=`` per test so the cross-test switch registry
    doesn't collide.
    """
    return LearnedSwitch(
        rule=handling_rule,
        labels=labels,
        author="dispatch-phase-tests",
        starting_phase=phase,
        model=model,
        ml_head=ml_head,
        auto_advance=False,
        auto_record=False,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 1. RULE
# ---------------------------------------------------------------------------


class TestDispatchFiresAtRulePhase:
    """Rule decides 'retry'; do_retry recorded with original FailureContext."""

    def test_rule_phase_dispatches_chosen_handler(self):
        fired, handlers = _build_handlers()
        sw = _make_switch(
            phase=Phase.RULE,
            labels=handlers,
            name="dispatch_rule_phase",
        )
        ctx = FailureContext("HTTPError", http_status=503, endpoint="/api/v1/users", attempt=1)
        result = sw.dispatch(ctx)

        assert result.label == "retry"
        assert result.phase is Phase.RULE
        assert result.source == "rule"
        assert len(fired["retry"]) == 1
        assert fired["retry"][0] is ctx
        # Only the matched handler fires.
        assert fired["fallback"] == []
        assert fired["escalate"] == []
        assert fired["drop"] == []
        assert result.action_result == "requeued /api/v1/users (attempt 2)"
        assert result.action_raised is None
        assert result.action_elapsed_ms is not None and result.action_elapsed_ms >= 0.0


# ---------------------------------------------------------------------------
# 2. MODEL_PRIMARY — model overrides rule and dispatches to model's pick.
# ---------------------------------------------------------------------------


class TestDispatchFiresAtModelPrimary:
    """Rule says 'retry'; stub model says 'escalate'; do_escalate fires, do_retry does NOT."""

    def test_model_primary_dispatches_model_label(self):
        fired, handlers = _build_handlers()
        # Confidence 0.95 > default threshold 0.85 so the model label wins.
        model = StubModel(label="escalate", confidence=0.95)
        sw = _make_switch(
            phase=Phase.MODEL_PRIMARY,
            model=model,
            labels=handlers,
            name="dispatch_model_primary",
        )
        ctx = FailureContext("HTTPError", http_status=503, endpoint="/api/v1/users", attempt=1)
        # Sanity: the rule alone would have returned 'retry'.
        assert handling_rule(ctx) == "retry"

        result = sw.dispatch(ctx)

        assert result.label == "escalate"
        assert result.phase is Phase.MODEL_PRIMARY
        assert result.source == "model"
        # do_escalate fired with the SAME object as the dispatch input.
        assert len(fired["escalate"]) == 1
        assert fired["escalate"][0] is ctx
        # do_retry did NOT fire — the model's verdict supplanted the rule.
        assert fired["retry"] == []
        assert result.action_raised is None


# ---------------------------------------------------------------------------
# 3. ML_PRIMARY — same shape with stub ML head deciding.
# ---------------------------------------------------------------------------


class TestDispatchFiresAtMLPrimary:
    """Rule says 'retry'; stub ML head says 'escalate'; do_escalate fires, do_retry does NOT."""

    def test_ml_primary_dispatches_ml_label(self):
        fired, handlers = _build_handlers()
        ml_head = StubMLHead(label="escalate", confidence=0.99)
        sw = _make_switch(
            phase=Phase.ML_PRIMARY,
            ml_head=ml_head,
            labels=handlers,
            name="dispatch_ml_primary",
        )
        ctx = FailureContext("HTTPError", http_status=503, endpoint="/api/v1/users", attempt=1)
        assert handling_rule(ctx) == "retry"

        result = sw.dispatch(ctx)

        assert result.label == "escalate"
        assert result.phase is Phase.ML_PRIMARY
        assert result.source == "ml"
        assert len(fired["escalate"]) == 1
        assert fired["escalate"][0] is ctx
        assert fired["retry"] == []
        assert result.action_raised is None


# ---------------------------------------------------------------------------
# 4. ML_WITH_FALLBACK cascade — low-confidence ML routes through MODEL_PRIMARY.
# ---------------------------------------------------------------------------


class TestDispatchAtMLWithFallbackCascade:
    """ML head returns low confidence; cascade falls to model (or rule_fallback) and dispatches the FINAL chosen handler."""

    def test_ml_with_fallback_cascade_dispatches_final_label(self):
        fired, handlers = _build_handlers()
        # ML head: low-confidence (< threshold 0.7) — cascade should fire.
        ml_head = StubMLHead(label="drop", confidence=0.3)
        # Model returns a high-confidence pick — the cascade lands at "model".
        model = StubModel(label="escalate", confidence=0.95)
        sw = _make_switch(
            phase=Phase.ML_WITH_FALLBACK,
            ml_head=ml_head,
            model=model,
            labels=handlers,
            confidence_threshold=0.7,
            name="dispatch_ml_with_fallback",
        )
        ctx = FailureContext("HTTPError", http_status=503, endpoint="/api/v1/users", attempt=1)
        result = sw.dispatch(ctx)

        # Cascade landed at the model's pick (escalate), not the ML head's pick (drop).
        assert result.label == "escalate"
        assert result.phase is Phase.ML_WITH_FALLBACK
        # source reflects the cascade — "model" (cascade landed at MODEL_PRIMARY)
        # or "rule_fallback" if the model also under-confidenced. With a stub
        # at 0.95 it should be "model".
        assert result.source in ("model", "rule_fallback")
        # The handler matching the FINAL chosen label fires.
        assert len(fired["escalate"]) == 1
        assert fired["escalate"][0] is ctx
        # The ML head's preferred ("drop") did NOT fire — its prediction was
        # below threshold and got dropped in the cascade.
        assert fired["drop"] == []
        assert fired["retry"] == []

    def test_ml_with_fallback_cascade_to_rule_fallback(self):
        """ML low-conf AND no model => cascade lands at rule_fallback; rule's pick dispatches."""
        fired, handlers = _build_handlers()
        ml_head = StubMLHead(label="drop", confidence=0.3)
        sw = _make_switch(
            phase=Phase.ML_WITH_FALLBACK,
            ml_head=ml_head,
            model=None,
            labels=handlers,
            confidence_threshold=0.7,
            name="dispatch_ml_with_fallback_rule",
        )
        ctx = FailureContext("HTTPError", http_status=503, endpoint="/api/v1/users", attempt=1)
        result = sw.dispatch(ctx)

        assert result.label == "retry"  # rule's pick
        assert result.phase is Phase.ML_WITH_FALLBACK
        assert result.source == "rule_fallback"
        assert len(fired["retry"]) == 1
        assert fired["retry"][0] is ctx
        assert fired["drop"] == []


# ---------------------------------------------------------------------------
# 5. Input identity across phases — handler receives the exact same object.
# ---------------------------------------------------------------------------


class TestInputIdentityAcrossPhases:
    """At every phase, the handler receives the SAME FailureContext instance — no copy / re-wrap."""

    @pytest.mark.parametrize(
        "phase",
        [Phase.RULE, Phase.MODEL_PRIMARY, Phase.ML_PRIMARY],
    )
    def test_handler_receives_input_identity(self, phase):
        fired, handlers = _build_handlers()
        # Configure adapters to return 'retry' at every phase so the same
        # handler fires regardless of which phase produced the label.
        model = StubModel(label="retry", confidence=0.95)
        ml_head = StubMLHead(label="retry", confidence=0.99)
        sw = _make_switch(
            phase=phase,
            model=model,
            ml_head=ml_head,
            labels=handlers,
            name=f"dispatch_identity_{phase.value.lower()}",
        )
        ctx = FailureContext("HTTPError", http_status=503, endpoint="/api/v1/users", attempt=1)
        result = sw.dispatch(ctx)

        assert result.label == "retry"
        assert result.phase is phase
        assert len(fired["retry"]) == 1
        # The handler received the SAME instance — no copying / re-wrapping
        # between rule entry and dispatch.
        assert fired["retry"][0] is ctx


# ---------------------------------------------------------------------------
# 6. Action exception capture across phases — handler raises, dispatch absorbs.
# ---------------------------------------------------------------------------


class TestActionExceptionCaptureAcrossPhases:
    """Handler raising at any phase: dispatch returns normally, action_raised populated, no propagation."""

    @pytest.mark.parametrize(
        "phase",
        [Phase.RULE, Phase.MODEL_PRIMARY, Phase.ML_PRIMARY],
    )
    def test_handler_exception_is_captured(self, phase):
        def boom(ctx):
            raise BillingDownError("billing service is down")

        # Replace the 'retry' handler with one that raises; keep the others
        # benign so we can observe non-firing.
        _, base_handlers = _build_handlers()
        labels = {**base_handlers, "retry": boom}

        model = StubModel(label="retry", confidence=0.95)
        ml_head = StubMLHead(label="retry", confidence=0.99)
        sw = _make_switch(
            phase=phase,
            model=model,
            ml_head=ml_head,
            labels=labels,
            name=f"dispatch_exception_{phase.value.lower()}",
        )
        ctx = FailureContext("HTTPError", http_status=503, endpoint="/api/v1/users", attempt=1)

        # Must not propagate — even at non-rule phases.
        result = sw.dispatch(ctx)

        assert result.label == "retry"
        assert result.phase is phase
        # Captured per the contract: "<ExcType>: <msg>".
        assert result.action_raised is not None
        assert "BillingDownError" in result.action_raised
        assert "billing service is down" in result.action_raised
        # action_result is None on raise; elapsed_ms is still recorded.
        assert result.action_result is None
        assert result.action_elapsed_ms is not None


# ---------------------------------------------------------------------------
# 7. classify() never fires actions — counter-test for the dispatch contract.
# ---------------------------------------------------------------------------


class TestClassifyDoesNotFireOnAnyPhase:
    """classify() returns the label but never invokes a handler — at every phase."""

    @pytest.mark.parametrize(
        "phase",
        [
            Phase.RULE,
            Phase.MODEL_SHADOW,
            Phase.MODEL_PRIMARY,
            Phase.ML_SHADOW,
            Phase.ML_WITH_FALLBACK,
            Phase.ML_PRIMARY,
        ],
    )
    def test_classify_does_not_fire_handlers(self, phase):
        fired, handlers = _build_handlers()
        # All adapters return 'retry' at high confidence so any phase that
        # were to dispatch would fire do_retry.
        model = StubModel(label="retry", confidence=0.95)
        ml_head = StubMLHead(label="retry", confidence=0.99)
        sw = _make_switch(
            phase=phase,
            model=model,
            ml_head=ml_head,
            labels=handlers,
            name=f"classify_pure_{phase.value.lower()}",
        )
        ctx = FailureContext("HTTPError", http_status=503, endpoint="/api/v1/users", attempt=1)

        result = sw.classify(ctx)

        # The classification result is populated.
        assert result.label  # any non-empty label
        assert result.phase is phase
        # No handler was invoked — at any phase.
        for strategy, fired_list in fired.items():
            assert fired_list == [], (
                f"phase={phase.value} fired handler for {strategy!r} on "
                f"classify(); classify() must never invoke actions."
            )
        # And the action-tracking fields stay empty on classify().
        assert result.action_result is None
        assert result.action_raised is None
        assert result.action_elapsed_ms is None


# ---------------------------------------------------------------------------
# 8. async adispatch parity — same shape as #2 but via adispatch + async handler.
# ---------------------------------------------------------------------------


class TestAsyncAdispatchParityAcrossPhases:
    """adispatch fires the handler at MODEL_PRIMARY, same contract as sync dispatch."""

    def test_adispatch_at_model_primary_dispatches_model_label(self):
        # adispatch's default impl wraps sync dispatch in a thread, and a
        # plain sync handler is what most production callers will pass.
        # Use a sync handler here for parity with the sync test (the
        # contract under test is "adispatch fires the chosen label",
        # not "the handler can be async" — adispatch off-loads the sync
        # action onto a worker thread).
        fired, handlers = _build_handlers()
        model = StubModel(label="escalate", confidence=0.95)
        sw = _make_switch(
            phase=Phase.MODEL_PRIMARY,
            model=model,
            labels=handlers,
            name="adispatch_model_primary",
        )
        ctx = FailureContext("HTTPError", http_status=503, endpoint="/api/v1/users", attempt=1)

        result = asyncio.run(sw.adispatch(ctx))

        assert result.label == "escalate"
        assert result.phase is Phase.MODEL_PRIMARY
        assert result.source == "model"
        assert len(fired["escalate"]) == 1
        assert fired["escalate"][0] is ctx
        assert fired["retry"] == []
        assert result.action_raised is None
