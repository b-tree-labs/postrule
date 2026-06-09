# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Tests for the LLM-as-teacher distillation-graduation loop (#132).

The controller makes teach -> distill -> graduate a first-class, automatic path:
run the expensive MODEL (LLM) tier only long enough to LABEL a stream, train the
cheap ML student on those labels, run it in shadow, and graduate off the LLM once
the student is statistically non-inferior (CostToleranceGate).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pytest

from postrule import (
    LearnedSwitch,
    MLPrediction,
    ModelPrediction,
    Phase,
)
from postrule.research import DistillationGraduation


@pytest.fixture(autouse=True)
def _no_telemetry(monkeypatch):
    # These tests drive hundreds of classify calls; keep the async verdict
    # telemetry sender from opening a socket to the cloud endpoint.
    monkeypatch.setenv("POSTRULE_NO_TELEMETRY", "1")


def rule(item: dict) -> str:
    # Weak day-zero rule: only catches the obvious "bug" keyword.
    return "bug" if "crash" in (item.get("t") or "").lower() else "other"


class TeacherLM:
    """Deterministic teacher: high-quality labels from the title keyword."""

    def classify(self, item: Any, _labels: Iterable[str]) -> ModelPrediction:
        t = (item.get("t") or "").lower()
        if "crash" in t or "error" in t:
            return ModelPrediction(label="bug", confidence=0.95)
        if "add" in t or "feature" in t:
            return ModelPrediction(label="feature", confidence=0.92)
        return ModelPrediction(label="question", confidence=0.9)


class PerfectStudent:
    """Student that, once fit, reproduces the teacher's labels exactly — so it
    is non-inferior and graduation should fire."""

    def __init__(self) -> None:
        self._table: dict[str, str] = {}

    def fit(self, records) -> None:
        for r in records:
            if r.model_output is not None and r.input is not None:
                self._table[(r.input or {}).get("t", "")] = r.model_output

    def predict(self, item, _labels=None) -> MLPrediction:
        return MLPrediction(label=self._table.get(item.get("t", ""), "other"), confidence=0.8)

    def model_version(self) -> str:
        return "perfect-student-1"


def _make_switch(name: str) -> LearnedSwitch:
    return LearnedSwitch(
        name=name,
        rule=rule,
        model=TeacherLM(),
        starting_phase=Phase.MODEL_PRIMARY,
    )


ITEMS = [
    ({"t": "app crash on login"}, "bug"),
    ({"t": "error in checkout"}, "bug"),
    ({"t": "add dark mode"}, "feature"),
    ({"t": "feature for export"}, "feature"),
    ({"t": "how do I reset?"}, "question"),
]


def _drive(switch: LearnedSwitch, rounds: int) -> None:
    """Stream labeled traffic through the switch via the result-aware verdict
    path, so per-call shadow observations (model_output, ml_output) are threaded
    into the log — the paired data the non-inferiority gate needs."""
    for i in range(rounds):
        item, truth = ITEMS[i % len(ITEMS)]
        res = switch.classify(item)
        if res.label == truth:
            res.mark_correct()
        else:
            res.mark_incorrect()


def test_holds_until_enough_teacher_labels():
    sw = _make_switch("distill-hold")
    _drive(sw, 20)  # fewer than min_teacher_labels
    ctrl = DistillationGraduation(sw, PerfectStudent(), min_teacher_labels=200)
    report = ctrl.step()
    assert not report.student_trained
    assert not report.graduated
    assert sw.phase() is Phase.MODEL_PRIMARY  # nothing happened yet


def test_trains_and_enters_shadow_then_graduates():
    sw = _make_switch("distill-go")
    _drive(sw, 250)  # plenty of teacher labels at MODEL_PRIMARY

    ctrl = DistillationGraduation(sw, PerfectStudent(), min_teacher_labels=200)

    # Step 1: train the student and move into ML_SHADOW so pairs accumulate.
    r1 = ctrl.step()
    assert r1.student_trained
    assert not r1.graduated
    assert sw.phase() is Phase.ML_SHADOW

    # Accumulate paired (teacher, student) observations in shadow.
    _drive(sw, 250)

    # Step 2: the non-inferior student graduates the switch off the LLM.
    r2 = ctrl.step()
    assert r2.graduated
    assert r2.decision is not None and r2.decision.target_better
    assert sw.phase() is Phase.ML_WITH_FALLBACK


def test_does_not_graduate_when_student_inferior():
    sw = _make_switch("distill-bad")
    _drive(sw, 250)

    class DumbStudent(PerfectStudent):
        def predict(self, item, _labels=None):
            return MLPrediction(label="other", confidence=0.5)  # always wrong

    ctrl = DistillationGraduation(sw, DumbStudent(), min_teacher_labels=200)
    ctrl.step()  # train + enter shadow
    _drive(sw, 250)
    r = ctrl.step()
    assert not r.graduated
    assert sw.phase() is Phase.ML_SHADOW  # stays shadowing; LLM still terminal


def test_rejects_non_ml_target_phase():
    sw = _make_switch("distill-badcfg")
    with pytest.raises(ValueError, match="ML terminal"):
        DistillationGraduation(sw, PerfectStudent(), target_phase=Phase.RULE)


def test_custom_target_phase_and_margin():
    sw = _make_switch("distill-cfg")
    _drive(sw, 250)
    from postrule.gates import CostToleranceGate

    ctrl = DistillationGraduation(
        sw,
        PerfectStudent(),
        min_teacher_labels=200,
        gate=CostToleranceGate(margin=0.05),
        target_phase=Phase.ML_PRIMARY,
    )
    ctrl.step()
    _drive(sw, 250)
    r = ctrl.step()
    assert r.graduated
    assert sw.phase() is Phase.ML_PRIMARY
