# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Tests for the confidence-gated escalation core (#198)."""

from __future__ import annotations

import pytest

from postrule.escalation import (
    ConfidenceGate,
    EscalationTarget,
    calibrate_threshold,
    due_for_hint,
    educational_hint,
    resolve_escalation,
)
from postrule.ml import MLPrediction


class TestEducationalHint:
    """Conservative, proactive education — distinct from the cap upsell."""

    def test_due_only_after_long_silence_on_established_account(self) -> None:
        assert due_for_hint(days_since_last_hint=45, account_age_days=90) is True

    def test_not_due_for_a_brand_new_account(self) -> None:
        # Don't lecture brand-new users even after silence.
        assert due_for_hint(days_since_last_hint=45, account_age_days=3) is False

    def test_not_due_if_recently_hinted(self) -> None:
        # At most ~one hint per long period — no nagging.
        assert due_for_hint(days_since_last_hint=5, account_age_days=365) is False

    def test_hint_is_educational_not_a_dun(self) -> None:
        m = educational_hint("your switches have graduated 3 rules to local.")
        low = m.lower()
        assert "learn more" in low and "http" in low
        assert "pay" not in low and "upgrade" not in low


def _pred(conf: float, label: str = "x") -> MLPrediction:
    return MLPrediction(label=label, confidence=conf)


class TestResolveEscalation:
    """The 'their code always runs (blind)' guarantee."""

    def test_no_escalation_returns_local(self) -> None:
        gate = ConfidenceGate(0.8)
        local = _pred(0.95, "local")
        out = resolve_escalation(local, gate.decide(local), lambda: _pred(0.99, "cloud"))
        assert out.prediction.label == "local"
        assert out.source == "local"
        assert out.escalated is False

    def test_successful_escalation_returns_cloud(self) -> None:
        gate = ConfidenceGate(0.8, target=EscalationTarget.CLOUD)
        local = _pred(0.3, "local")
        out = resolve_escalation(local, gate.decide(local), lambda: _pred(0.99, "cloud"))
        assert out.prediction.label == "cloud"
        assert out.source == "escalated:cloud"
        assert out.escalated is True

    def test_cap_429_or_any_failure_falls_back_to_local(self) -> None:
        # The load-bearing case: escalation raises (cap 429, network, etc.) ->
        # caller still gets the local answer, never an exception.
        gate = ConfidenceGate(0.8)
        local = _pred(0.3, "local")

        def boom() -> MLPrediction:
            raise RuntimeError("HTTP 429 monthly_cap_exceeded")

        out = resolve_escalation(local, gate.decide(local), boom)
        assert out.prediction.label == "local"
        assert out.source == "local_fallback"
        assert out.escalated is False

    def test_no_call_provided_returns_local(self) -> None:
        gate = ConfidenceGate(0.8)
        local = _pred(0.1, "local")
        out = resolve_escalation(local, gate.decide(local), None)
        assert out.prediction.label == "local"
        assert out.escalated is False

    def test_authenticated_owner_gets_polite_upsell_on_fallback(self) -> None:
        gate = ConfidenceGate(0.8)
        local = _pred(0.3, "local")
        msgs: list[str] = []

        def boom() -> MLPrediction:
            raise RuntimeError("HTTP 429")

        resolve_escalation(local, gate.decide(local), boom, on_fallback=msgs.append)
        assert len(msgs) == 1
        m = msgs[0].lower()
        assert "postrule" in m and "one click" in m
        assert "pay up" not in m and "pay now" not in m  # upsell, not a dun
        assert "https://" in msgs[0]  # one-click resolve link present

    def test_keyless_consumer_is_never_nagged(self) -> None:
        # The 2nd-degree-consumer protection: no notifier (unauthenticated /
        # library-embedded) -> fallback is SILENT.
        gate = ConfidenceGate(0.8)
        local = _pred(0.3, "local")

        def boom() -> MLPrediction:
            raise RuntimeError("HTTP 429")

        # on_fallback omitted -> no exception, no nag, still returns local.
        out = resolve_escalation(local, gate.decide(local), boom)
        assert out.source == "local_fallback"

    def test_notifier_failure_never_breaks_the_run(self) -> None:
        gate = ConfidenceGate(0.8)
        local = _pred(0.3, "local")

        def boom() -> MLPrediction:
            raise RuntimeError("HTTP 429")

        def bad_notifier(_msg: str) -> None:
            raise RuntimeError("logging is broken")

        out = resolve_escalation(local, gate.decide(local), boom, on_fallback=bad_notifier)
        assert out.prediction.label == "local"  # still degrades gracefully


class TestConfidenceGate:
    def test_escalates_below_threshold(self) -> None:
        gate = ConfidenceGate(0.8)
        d = gate.decide(_pred(0.5))
        assert d.escalate is True
        assert d.threshold == 0.8
        assert "escalate" in d.reason

    def test_keeps_local_at_or_above_threshold(self) -> None:
        gate = ConfidenceGate(0.8)
        assert gate.decide(_pred(0.8)).escalate is False  # boundary: >= keeps
        assert gate.decide(_pred(0.95)).escalate is False

    def test_target_plumbs_through(self) -> None:
        gate = ConfidenceGate(0.8, target=EscalationTarget.LOCAL_ONLY)
        d = gate.decide(_pred(0.1))
        assert d.target is EscalationTarget.LOCAL_ONLY
        assert d.escalate is True

    @pytest.mark.parametrize("bad", [-0.1, 1.1, 2.0])
    def test_rejects_out_of_range_threshold(self, bad: float) -> None:
        with pytest.raises(ValueError):
            ConfidenceGate(bad)


class TestCalibrateThreshold:
    def test_picks_lowest_tau_meeting_floor_max_coverage(self) -> None:
        # Low-confidence items are the wrong ones; high-confidence are right.
        # conf:   0.2  0.4  0.6  0.8  0.9
        # right:   F    F    T    T    T
        confidences = [0.2, 0.4, 0.6, 0.8, 0.9]
        correct = [False, False, True, True, True]
        # Floor 1.0: must keep only the all-correct tail -> tau = 0.6
        assert calibrate_threshold(confidences, correct, 1.0) == 0.6
        # Floor 0.5: keeping ALL from tau=0.2 gives {F,F,T,T,T}=60% >= 50%,
        # which is max coverage -> tau=0.2 (lowest threshold that still meets it).
        assert calibrate_threshold(confidences, correct, 0.5) == 0.2

    def test_unachievable_floor_escalates_everything(self) -> None:
        # Even the most confident prediction is wrong -> no tau meets a high
        # floor; return tau just above max confidence so the gate escalates all.
        confidences = [0.3, 0.6, 0.9]
        correct = [False, False, False]
        tau = calibrate_threshold(confidences, correct, 0.9)
        assert tau > 0.9
        assert ConfidenceGate(min(tau, 1.0)).decide(_pred(0.9)).escalate is True

    def test_perfect_head_keeps_all(self) -> None:
        confidences = [0.51, 0.7, 0.99]
        correct = [True, True, True]
        # Floor satisfiable at the lowest tau -> keep everything.
        assert calibrate_threshold(confidences, correct, 0.9) == 0.51

    def test_validation_errors(self) -> None:
        with pytest.raises(ValueError):
            calibrate_threshold([0.5], [True, False], 0.9)  # length mismatch
        with pytest.raises(ValueError):
            calibrate_threshold([], [], 0.9)  # empty
        with pytest.raises(ValueError):
            calibrate_threshold([0.5], [True], 1.5)  # floor out of range
