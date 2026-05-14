# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Tests for the LLM-as-teacher bootstrapping pattern.

Verifies ``train_ml_from_model_outcomes`` correctly filters the outcome
log to LLM-labeled records and trains only when the minimum-sample
threshold is met.
"""

from __future__ import annotations

import time

from postrule import (
    ClassificationRecord,
    InMemoryStorage,
    LearnedSwitch,
    ModelPrediction,
)
from postrule.research import train_ml_from_model_outcomes


class _FakeMLHead:
    def __init__(self):
        self.trained_count: int = 0
        self.trained_records: list = []

    def fit(self, records):
        records = list(records)
        self.trained_count = len(records)
        self.trained_records = records

    def predict(self, input, labels):
        return ModelPrediction(label="stub", confidence=1.0)  # unused

    def model_version(self) -> str:
        return "fake"


def _rule(x: str) -> str:
    return "fallback"


def _make_switch() -> LearnedSwitch:
    return LearnedSwitch(
        name="triage",
        rule=_rule,
        author="@test:teacher",
        storage=InMemoryStorage(),
    )


def _write_outcomes(switch: LearnedSwitch, n: int, source: str, outcome: str):
    for i in range(n):
        switch.storage.append_record(
            switch.name,
            ClassificationRecord(
                timestamp=time.time(),
                input=f"input {i}",
                label="label_a",
                outcome=outcome,
                source=source,
                confidence=0.9,
            ),
        )


# ---------------------------------------------------------------------------


class TestLMAsTeacherHelper:
    def test_fits_when_model_outcomes_meet_threshold(self):
        sw = _make_switch()
        _write_outcomes(sw, 250, source="model", outcome="correct")

        head = _FakeMLHead()
        used = train_ml_from_model_outcomes(switch=sw, ml_head=head, min_llm_outcomes=200)
        assert used == 250
        assert head.trained_count == 250

    def test_skips_fit_when_below_threshold(self):
        sw = _make_switch()
        _write_outcomes(sw, 50, source="model", outcome="correct")

        head = _FakeMLHead()
        used = train_ml_from_model_outcomes(switch=sw, ml_head=head, min_llm_outcomes=200)
        assert used == 0
        assert head.trained_count == 0

    def test_filters_out_non_model_outcomes(self):
        sw = _make_switch()
        _write_outcomes(sw, 100, source="model", outcome="correct")
        _write_outcomes(sw, 500, source="rule", outcome="correct")

        head = _FakeMLHead()
        used = train_ml_from_model_outcomes(switch=sw, ml_head=head, min_llm_outcomes=50)
        # Only the 100 LLM records qualify; the 500 rule records are
        # ignored (this is the LLM-as-teacher intent — train only on
        # LLM-labeled data).
        assert used == 100

    def test_filters_out_incorrect_outcomes(self):
        sw = _make_switch()
        _write_outcomes(sw, 300, source="model", outcome="correct")
        _write_outcomes(sw, 200, source="model", outcome="incorrect")
        _write_outcomes(sw, 100, source="model", outcome="unknown")

        head = _FakeMLHead()
        used = train_ml_from_model_outcomes(switch=sw, ml_head=head, min_llm_outcomes=100)
        # Default filter keeps only "correct" outcomes.
        assert used == 300

    def test_custom_outcome_filter_accepted(self):
        sw = _make_switch()
        _write_outcomes(sw, 100, source="model", outcome="correct")
        _write_outcomes(sw, 50, source="model", outcome="unknown")

        head = _FakeMLHead()
        used = train_ml_from_model_outcomes(
            switch=sw,
            ml_head=head,
            min_llm_outcomes=100,
            outcome_label_filter=("correct", "unknown"),
        )
        assert used == 150

    def test_empty_log_returns_zero(self):
        sw = _make_switch()
        head = _FakeMLHead()
        used = train_ml_from_model_outcomes(switch=sw, ml_head=head, min_llm_outcomes=100)
        assert used == 0
        assert head.trained_count == 0
