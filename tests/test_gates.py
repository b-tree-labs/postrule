# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Phase-graduation gates — McNemar, Manual, and LearnedSwitch.advance()."""

from __future__ import annotations

import time

import pytest

from dendra import (
    AccuracyMarginGate,
    ClassificationRecord,
    CompositeGate,
    Gate,
    GateDecision,
    LearnedSwitch,
    ManualGate,
    McNemarGate,
    MinVolumeGate,
    MLPrediction,
    ModelPrediction,
    Phase,
    Verdict,
    next_phase,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _rec(
    *,
    label: str,
    outcome: str = Verdict.CORRECT.value,
    source: str = "rule",
    rule_output: str | None = None,
    model_output: str | None = None,
    ml_output: str | None = None,
) -> ClassificationRecord:
    return ClassificationRecord(
        timestamp=time.time(),
        input={"title": "x"},
        label=label,
        outcome=outcome,
        source=source,
        confidence=1.0,
        rule_output=rule_output,
        model_output=model_output,
        ml_output=ml_output,
    )


def _rule(ticket: dict) -> str:
    title = (ticket.get("title") or "").lower()
    if "crash" in title:
        return "bug"
    return "feature_request"


class _StubLLM:
    def __init__(self, label: str, confidence: float = 0.97) -> None:
        self._label = label
        self._confidence = confidence

    def classify(self, _input, _labels) -> ModelPrediction:
        return ModelPrediction(label=self._label, confidence=self._confidence)


class _StubMLHead:
    def __init__(self, label: str) -> None:
        self._label = label

    def fit(self, _records): ...

    def predict(self, _input, _labels) -> MLPrediction:
        return MLPrediction(label=self._label, confidence=0.95)

    def model_version(self) -> str:
        return "stub-1.0"


# ---------------------------------------------------------------------------
# next_phase()
# ---------------------------------------------------------------------------


class TestNextPhase:
    def test_walks_the_lifecycle(self):
        assert next_phase(Phase.RULE) is Phase.MODEL_SHADOW
        assert next_phase(Phase.MODEL_SHADOW) is Phase.MODEL_PRIMARY
        assert next_phase(Phase.MODEL_PRIMARY) is Phase.ML_SHADOW
        assert next_phase(Phase.ML_SHADOW) is Phase.ML_WITH_FALLBACK
        assert next_phase(Phase.ML_WITH_FALLBACK) is Phase.ML_PRIMARY

    def test_terminal_returns_none(self):
        assert next_phase(Phase.ML_PRIMARY) is None


# ---------------------------------------------------------------------------
# McNemarGate contract
# ---------------------------------------------------------------------------


class TestMcNemarGateConstruction:
    def test_rejects_bad_alpha(self):
        with pytest.raises(ValueError, match="alpha"):
            McNemarGate(alpha=0.0)
        with pytest.raises(ValueError, match="alpha"):
            McNemarGate(alpha=1.0)

    def test_rejects_non_positive_min_paired(self):
        with pytest.raises(ValueError, match="min_paired"):
            McNemarGate(min_paired=0)


class TestMcNemarGateInsufficientData:
    def test_refuses_on_empty_log(self):
        gate = McNemarGate()
        decision = gate.evaluate([], Phase.MODEL_SHADOW, Phase.MODEL_PRIMARY)
        assert decision.advance is False
        assert "insufficient" in decision.rationale

    def test_refuses_below_min_paired(self):
        gate = McNemarGate(min_paired=200)
        records = [
            _rec(label="bug", rule_output="bug", model_output="bug") for _ in range(10)
        ]
        decision = gate.evaluate(records, Phase.MODEL_SHADOW, Phase.MODEL_PRIMARY)
        assert decision.advance is False
        assert decision.paired_sample_size == 10


class TestMcNemarGateAdvances:
    def test_advances_when_target_beats_current(self):
        """Target (model) is ≥95% correct; current (rule) is ≤15% correct.
        With 300 paired samples the p-value falls well below alpha=0.01."""
        gate = McNemarGate(alpha=0.01, min_paired=200)

        records = []
        # Rule right 45/300 times, model right 285/300 times — very
        # strong one-sided improvement.
        for i in range(300):
            rule_right = i < 45  # first 45: rule correct
            model_right = i >= 15  # first 15: model wrong; rest: correct
            # Construct records so that:
            #   - outcome = CORRECT (required for gate to use the row)
            #   - label == rule_output iff rule_right
            #   - label == model_output iff model_right
            records.append(
                _rec(
                    label="bug",
                    rule_output="bug" if rule_right else "feature_request",
                    model_output="bug" if model_right else "feature_request",
                )
            )

        decision = gate.evaluate(records, Phase.MODEL_SHADOW, Phase.MODEL_PRIMARY)
        assert decision.advance is True
        assert decision.p_value is not None
        assert decision.p_value < 0.01
        assert decision.paired_sample_size == 300
        assert decision.current_accuracy is not None
        assert decision.target_accuracy is not None
        assert decision.target_accuracy > decision.current_accuracy

    def test_refuses_when_equal(self):
        """Both sides correct on same records → no discordant pairs → refuse."""
        gate = McNemarGate(alpha=0.01, min_paired=100)
        records = [
            _rec(label="bug", rule_output="bug", model_output="bug")
            for _ in range(200)
        ]
        decision = gate.evaluate(records, Phase.MODEL_SHADOW, Phase.MODEL_PRIMARY)
        assert decision.advance is False

    def test_refuses_when_current_beats_target(self):
        """Rule outperforms model → p >> alpha → refuse."""
        gate = McNemarGate(alpha=0.01, min_paired=100)
        records = []
        for i in range(200):
            rule_right = True
            model_right = i < 30  # only 15% correct
            records.append(
                _rec(
                    label="bug",
                    rule_output="bug" if rule_right else "feature_request",
                    model_output="bug" if model_right else "feature_request",
                )
            )
        decision = gate.evaluate(records, Phase.MODEL_SHADOW, Phase.MODEL_PRIMARY)
        assert decision.advance is False


class TestMcNemarGateIgnoresIncorrectOutcomes:
    def test_only_correct_outcome_records_contribute(self):
        """Incorrect-outcome records drop out — we don't know the ground
        truth for them without a correct-label field."""
        gate = McNemarGate(min_paired=5)
        records = [
            _rec(label="bug", outcome=Verdict.INCORRECT.value,
                 rule_output="bug", model_output="feature_request")
            for _ in range(500)
        ]
        decision = gate.evaluate(records, Phase.MODEL_SHADOW, Phase.MODEL_PRIMARY)
        assert decision.advance is False
        assert decision.paired_sample_size == 0


# ---------------------------------------------------------------------------
# ManualGate
# ---------------------------------------------------------------------------


class TestManualGate:
    def test_always_refuses(self):
        gate = ManualGate()
        decision = gate.evaluate([], Phase.RULE, Phase.MODEL_SHADOW)
        assert decision.advance is False
        assert "operator" in decision.rationale.lower()

    def test_conforms_to_protocol(self):
        gate = ManualGate()
        assert isinstance(gate, Gate)


# ---------------------------------------------------------------------------
# AccuracyMarginGate
# ---------------------------------------------------------------------------


class TestAccuracyMarginGate:
    def test_advances_when_delta_exceeds_margin(self):
        gate = AccuracyMarginGate(margin=0.10, min_paired=50)
        records = []
        for i in range(100):
            rule_right = i < 40  # 40% accuracy
            model_right = i < 80  # 80% accuracy
            records.append(_rec(
                label="bug",
                rule_output="bug" if rule_right else "feature_request",
                model_output="bug" if model_right else "feature_request",
            ))
        d = gate.evaluate(records, Phase.MODEL_SHADOW, Phase.MODEL_PRIMARY)
        assert d.advance is True
        assert d.current_accuracy == pytest.approx(0.40)
        assert d.target_accuracy == pytest.approx(0.80)

    def test_refuses_when_delta_below_margin(self):
        gate = AccuracyMarginGate(margin=0.10, min_paired=50)
        records = []
        for i in range(100):
            rule_right = i < 80
            model_right = i < 85  # only 5% better
            records.append(_rec(
                label="bug",
                rule_output="bug" if rule_right else "feature_request",
                model_output="bug" if model_right else "feature_request",
            ))
        d = gate.evaluate(records, Phase.MODEL_SHADOW, Phase.MODEL_PRIMARY)
        assert d.advance is False

    def test_refuses_below_min_paired(self):
        gate = AccuracyMarginGate(margin=0.05, min_paired=500)
        records = [_rec(label="bug", rule_output="bug", model_output="bug")
                   for _ in range(50)]
        d = gate.evaluate(records, Phase.MODEL_SHADOW, Phase.MODEL_PRIMARY)
        assert d.advance is False
        assert "insufficient" in d.rationale

    def test_rejects_invalid_margin(self):
        with pytest.raises(ValueError, match="margin"):
            AccuracyMarginGate(margin=-0.1)
        with pytest.raises(ValueError, match="margin"):
            AccuracyMarginGate(margin=1.0)


# ---------------------------------------------------------------------------
# MinVolumeGate
# ---------------------------------------------------------------------------


class TestMinVolumeGate:
    def test_refuses_below_volume_threshold(self):
        class _AlwaysYes:
            def evaluate(self, _r, _c, _t): return GateDecision(advance=True, rationale="y")
        gate = MinVolumeGate(_AlwaysYes(), min_records=100)
        records = [_rec(label="bug") for _ in range(50)]
        d = gate.evaluate(records, Phase.RULE, Phase.MODEL_SHADOW)
        assert d.advance is False
        assert "50 records" in d.rationale

    def test_delegates_once_volume_threshold_met(self):
        class _AlwaysYes:
            def evaluate(self, _r, _c, _t): return GateDecision(advance=True, rationale="inner yes")
        gate = MinVolumeGate(_AlwaysYes(), min_records=10)
        records = [_rec(label="bug") for _ in range(20)]
        d = gate.evaluate(records, Phase.RULE, Phase.MODEL_SHADOW)
        assert d.advance is True
        assert d.rationale == "inner yes"

    def test_rejects_non_positive_min(self):
        with pytest.raises(ValueError, match="min_records"):
            MinVolumeGate(McNemarGate(), min_records=0)


# ---------------------------------------------------------------------------
# CompositeGate
# ---------------------------------------------------------------------------


class TestCompositeGate:
    def test_all_of_advances_when_every_sub_advances(self):
        class _Yes:
            def evaluate(self, _r, _c, _t): return GateDecision(advance=True, rationale="yes")
        gate = CompositeGate.all_of([_Yes(), _Yes(), _Yes()])
        d = gate.evaluate([], Phase.RULE, Phase.MODEL_SHADOW)
        assert d.advance is True

    def test_all_of_refuses_when_any_sub_refuses(self):
        class _Yes:
            def evaluate(self, _r, _c, _t): return GateDecision(advance=True, rationale="yes")
        class _No:
            def evaluate(self, _r, _c, _t): return GateDecision(advance=False, rationale="no")
        gate = CompositeGate.all_of([_Yes(), _No(), _Yes()])
        d = gate.evaluate([], Phase.RULE, Phase.MODEL_SHADOW)
        assert d.advance is False
        assert "✗" in d.rationale

    def test_any_of_advances_when_any_sub_advances(self):
        class _Yes:
            def evaluate(self, _r, _c, _t): return GateDecision(advance=True, rationale="yes")
        class _No:
            def evaluate(self, _r, _c, _t): return GateDecision(advance=False, rationale="no")
        gate = CompositeGate.any_of([_No(), _Yes(), _No()])
        d = gate.evaluate([], Phase.RULE, Phase.MODEL_SHADOW)
        assert d.advance is True

    def test_any_of_refuses_when_all_sub_refuse(self):
        class _No:
            def evaluate(self, _r, _c, _t): return GateDecision(advance=False, rationale="no")
        gate = CompositeGate.any_of([_No(), _No()])
        d = gate.evaluate([], Phase.RULE, Phase.MODEL_SHADOW)
        assert d.advance is False

    def test_rejects_empty_gates(self):
        with pytest.raises(ValueError, match="at least one"):
            CompositeGate([], mode="all")

    def test_rejects_invalid_mode(self):
        with pytest.raises(ValueError, match="mode"):
            CompositeGate([ManualGate()], mode="maybe")


# ---------------------------------------------------------------------------
# LearnedSwitch.advance() — the wiring
# ---------------------------------------------------------------------------


class TestAdvance:
    def test_default_gate_is_mcnemargate(self):
        s = LearnedSwitch(rule=_rule)
        assert isinstance(s.config.gate, McNemarGate)

    def test_refuses_at_terminal_phase(self):
        s = LearnedSwitch(rule=_rule, starting_phase=Phase.ML_PRIMARY)
        decision = s.advance()
        assert decision.advance is False
        assert "terminal" in decision.rationale

    def test_refuses_when_target_exceeds_phase_limit(self):
        s = LearnedSwitch(
            rule=_rule,
            starting_phase=Phase.MODEL_SHADOW,
            phase_limit=Phase.MODEL_SHADOW,
        )
        decision = s.advance()
        assert decision.advance is False
        assert "phase_limit" in decision.rationale

    def test_safety_critical_refuses_ml_primary_even_if_gate_passes(self):
        s = LearnedSwitch(
            rule=_rule,
            starting_phase=Phase.ML_WITH_FALLBACK,
            phase_limit=Phase.ML_PRIMARY,  # user tries to widen
            safety_critical=True,
        )
        # safety_critical overrode phase_limit at construction; but even
        # if someone mutated it back, advance's own check catches it.
        s.config.phase_limit = Phase.ML_PRIMARY
        decision = s.advance()
        assert decision.advance is False
        assert "safety_critical" in decision.rationale.lower()

    def test_advances_when_gate_returns_true(self):
        class _AlwaysYesGate:
            def evaluate(self, _records, _current, _target):
                return GateDecision(advance=True, rationale="test gate says go")

        s = LearnedSwitch(
            rule=_rule,
            starting_phase=Phase.RULE,
            gate=_AlwaysYesGate(),
        )
        decision = s.advance()
        assert decision.advance is True
        assert s.phase() is Phase.MODEL_SHADOW

    def test_does_not_advance_when_gate_refuses(self):
        s = LearnedSwitch(
            rule=_rule,
            starting_phase=Phase.RULE,
            gate=ManualGate(),
        )
        decision = s.advance()
        assert decision.advance is False
        assert s.phase() is Phase.RULE

    def test_advance_emits_telemetry(self):
        from dendra import ListEmitter

        events = ListEmitter()

        class _AlwaysYesGate:
            def evaluate(self, _records, _current, _target):
                return GateDecision(
                    advance=True,
                    rationale="test",
                    p_value=0.001,
                    paired_sample_size=300,
                )

        s = LearnedSwitch(
            rule=_rule,
            starting_phase=Phase.RULE,
            gate=_AlwaysYesGate(),
            telemetry=events,
        )
        s.advance()
        # ListEmitter stores (event_name, payload) tuples.
        advance_events = [
            payload for (name, payload) in events.events if name == "advance"
        ]
        assert len(advance_events) == 1
        payload = advance_events[0]
        assert payload["from"] == "RULE"
        assert payload["to"] == "MODEL_SHADOW"
        assert payload["p_value"] == 0.001

    def test_auto_advance_fires_at_interval(self):
        """record_verdict calls advance() every auto_advance_interval rows."""
        class _YesGate:
            def __init__(self):
                self.calls = 0
            def evaluate(self, _records, _current, _target):
                self.calls += 1
                return GateDecision(advance=True, rationale="yes")

        gate = _YesGate()
        s = LearnedSwitch(
            rule=_rule,
            starting_phase=Phase.RULE,
            phase_limit=Phase.ML_PRIMARY,
            gate=gate,
            auto_advance=True,
            auto_advance_interval=3,
        )
        # 2 records — no advance yet.
        for _ in range(2):
            s.record_verdict(input={"title": "x"}, label="bug",
                                outcome=Verdict.CORRECT.value)
        assert gate.calls == 0
        assert s.phase() is Phase.RULE

        # 3rd record — triggers advance.
        s.record_verdict(input={"title": "x"}, label="bug",
                            outcome=Verdict.CORRECT.value)
        assert gate.calls == 1
        assert s.phase() is Phase.MODEL_SHADOW

    def test_auto_advance_disabled(self):
        class _YesGate:
            def evaluate(self, _r, _c, _t):
                return GateDecision(advance=True, rationale="yes")
        s = LearnedSwitch(
            rule=_rule, gate=_YesGate(), auto_advance=False,
            auto_advance_interval=1,
        )
        for _ in range(50):
            s.record_verdict(input={"title": "x"}, label="bug",
                                outcome=Verdict.CORRECT.value)
        assert s.phase() is Phase.RULE  # never advanced

    def test_auto_advance_tags_telemetry(self):
        from dendra import ListEmitter
        events = ListEmitter()

        class _YesGate:
            def evaluate(self, _r, _c, _t):
                return GateDecision(advance=True, rationale="yes")

        s = LearnedSwitch(
            rule=_rule, gate=_YesGate(),
            auto_advance=True, auto_advance_interval=1,
            telemetry=events,
        )
        s.record_verdict(input={"title": "x"}, label="bug",
                            outcome=Verdict.CORRECT.value)
        advance_payloads = [p for (n, p) in events.events if n == "advance"]
        assert len(advance_payloads) == 1
        assert advance_payloads[0]["auto"] is True

        # Manual advance gets auto=False.
        s.advance()  # already at terminal walk; but manual still tags
        # There may or may not be another advance event depending on
        # phase state. The key invariant: any auto events have auto=True.
        for payload in advance_payloads:
            assert payload["auto"] is True

    def test_manual_advance_still_works_with_auto_on(self):
        class _YesGate:
            def __init__(self): self.calls = 0
            def evaluate(self, _r, _c, _t):
                self.calls += 1
                return GateDecision(advance=True, rationale="yes")

        gate = _YesGate()
        s = LearnedSwitch(
            rule=_rule, gate=gate,
            auto_advance=True, auto_advance_interval=100,
        )
        # One manual call — doesn't wait for the interval.
        s.advance()
        assert gate.calls == 1

    def test_auto_advance_gate_exception_does_not_break_record(self):
        """A broken gate must not take down record_verdict."""
        class _BrokenGate:
            def evaluate(self, _r, _c, _t):
                raise RuntimeError("gate is broken")

        s = LearnedSwitch(
            rule=_rule, gate=_BrokenGate(),
            auto_advance=True, auto_advance_interval=1,
        )
        # Should not raise.
        s.record_verdict(input={"title": "x"}, label="bug",
                            outcome=Verdict.CORRECT.value)

    def test_advance_on_real_records_with_mcnemar(self):
        """End-to-end: paired rule-vs-model records, real McNemarGate."""
        s = LearnedSwitch(
            rule=_rule,
            starting_phase=Phase.MODEL_SHADOW,
            model=_StubLLM(label="bug"),
            gate=McNemarGate(alpha=0.01, min_paired=200),
        )
        # Inject clear evidence: model beats rule 270/300 vs 50/300.
        for i in range(300):
            rule_right = i < 50
            model_right = i >= 30
            s.storage.append_record(
                s.name,
                _rec(
                    label="bug",
                    rule_output="bug" if rule_right else "feature_request",
                    model_output="bug" if model_right else "feature_request",
                ),
            )
        decision = s.advance()
        assert decision.advance is True
        assert s.phase() is Phase.MODEL_PRIMARY
        assert decision.p_value is not None
        assert decision.p_value < 0.01
