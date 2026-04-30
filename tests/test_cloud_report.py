# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: LicenseRef-BSL-1.1

"""Tests for ``dendra.cloud.report`` — Phase 1 markdown report card.

Covers: aggregator output shape, day-zero (no-records) handling,
gate-fire detection, crossover detection, hypothesis-vs-observed
verdict, markdown rendering for each scenario.
"""

from __future__ import annotations

import time

import pytest

from dendra.cloud.report import (
    HypothesisVerdict,
    aggregate_switch,
    render_switch_card,
)
from dendra.cloud.report.aggregator import _is_correct
from dendra.core import ClassificationRecord, Phase
from dendra.storage import InMemoryStorage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _record(
    *,
    label: str,
    outcome: str,
    source: str = "rule",
    rule_output: str | None = None,
    ml_output: str | None = None,
    timestamp: float | None = None,
) -> ClassificationRecord:
    return ClassificationRecord(
        timestamp=timestamp if timestamp is not None else time.time(),
        input={"text": "x"},
        label=label,
        outcome=outcome,
        source=source,
        confidence=1.0,
        rule_output=rule_output,
        model_output=None,
        ml_output=ml_output,
    )


@pytest.fixture
def empty_storage():
    return InMemoryStorage()


@pytest.fixture
def graduated_storage():
    """Storage with synthetic records simulating a clean graduation.

    100 outcome="correct" records (label always "A"; verdict source
    confirmed the chosen label was right). Distribution of
    rule_output / ml_output:

      - 60 rows: both correct (rule_output="A", ml_output="A") — concordant
      - 25 rows: rule wrong, ml right (rule_output="B", ml_output="A")
                 — discordant favoring ML
      - 15 rows: rule right, ml wrong (rule_output="A", ml_output="B")
                 — discordant favoring rule

    Net 25 vs 15 discordant pairs → McNemar's exact p ≈ 0.15 with
    n_disc=40; with binomial(40, 0.5) the two-sided p for ≥25 is
    around 0.15, so we set min_paired low and alpha generous to fire.
    For a clearer fire, use 80 vs 20 split — McNemar p ≈ 1.4e-9.
    """
    s = InMemoryStorage()
    base_ts = time.time() - 86400  # 1 day ago
    # Pattern: 60 concordant-correct, 30 ML-favored, 10 rule-favored
    # → 30 vs 10 discordant. McNemar two-sided exact p ≈ 1.5e-3.
    config: list[tuple[str, str]] = (
        [("A", "A")] * 60 + [("B", "A")] * 30 + [("A", "B")] * 10
    )
    for i, (rule_out, ml_out) in enumerate(config):
        s.append_record(
            "test_switch",
            _record(
                label="A",
                outcome="correct",
                source="rule",
                rule_output=rule_out,
                ml_output=ml_out,
                timestamp=base_ts + i * 60,
            ),
        )
    return s


@pytest.fixture
def pre_graduation_storage():
    """Storage with too few records to clear the gate."""
    s = InMemoryStorage()
    base_ts = time.time() - 3600
    for i in range(20):  # well below min_paired threshold
        s.append_record(
            "early_switch",
            _record(
                label="A",
                outcome="correct",
                source="rule",
                rule_output="A",
                ml_output="A",
                timestamp=base_ts + i * 60,
            ),
        )
    return s


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


class TestAggregator:
    def test_empty_storage_returns_day_zero_metrics(self, empty_storage):
        m = aggregate_switch(empty_storage, "no_such_switch")
        assert m.total_outcomes == 0
        assert m.checkpoints == []
        assert m.gate_fire_outcome is None
        assert m.current_phase == Phase.RULE
        assert m.rule_accuracy_final is None
        assert m.ml_accuracy_final is None

    def test_day_zero_with_explicit_phase(self, empty_storage):
        m = aggregate_switch(
            empty_storage, "no_such_switch", current_phase=Phase.MODEL_SHADOW
        )
        assert m.current_phase == Phase.MODEL_SHADOW

    def test_gate_fires_on_clear_signal(self, graduated_storage):
        m = aggregate_switch(graduated_storage, "test_switch", min_paired=10, alpha=0.05)
        assert m.total_outcomes == 100
        assert len(m.checkpoints) >= 2
        assert m.gate_fire_outcome is not None
        assert m.gate_fire_p_value is not None
        assert m.gate_fire_p_value < 0.05

    def test_checkpoint_count_matches_total_over_step(self, graduated_storage):
        m = aggregate_switch(graduated_storage, "test_switch", checkpoint_every=50)
        # 100 outcomes / 50 step = 2 full checkpoints
        assert len(m.checkpoints) == 2
        assert m.checkpoints[0].outcome_count == 50
        assert m.checkpoints[1].outcome_count == 100

    def test_partial_window_checkpoint_emitted(self):
        """When total isn't a multiple of step, the final partial window
        gets its own checkpoint so the table includes the latest state."""
        s = InMemoryStorage()
        for i in range(123):
            s.append_record(
                "x",
                _record(label="A", outcome="correct", rule_output="A", ml_output="A"),
            )
        m = aggregate_switch(s, "x", checkpoint_every=50)
        assert [c.outcome_count for c in m.checkpoints] == [50, 100, 123]

    def test_rule_and_ml_accuracy_from_paired_records(self, graduated_storage):
        m = aggregate_switch(graduated_storage, "test_switch")
        assert m.rule_accuracy_final is not None
        assert m.ml_accuracy_final is not None
        assert m.ml_accuracy_final > m.rule_accuracy_final

    def test_pre_graduation_no_gate_fire(self, pre_graduation_storage):
        m = aggregate_switch(pre_graduation_storage, "early_switch", min_paired=30)
        # Below min_paired threshold → no gate fire
        assert m.gate_fire_outcome is None

    def test_unknown_outcomes_excluded_from_accuracy(self):
        s = InMemoryStorage()
        for _ in range(50):
            s.append_record(
                "x", _record(label="A", outcome="unknown", rule_output="A", ml_output="A")
            )
        m = aggregate_switch(s, "x")
        # All records have outcome="unknown" → no accuracy data
        assert m.rule_accuracy_final is None
        assert m.ml_accuracy_final is None


class TestIsCorrect:
    def test_correct_outcome_matches_when_prediction_equals_label(self):
        r = _record(label="A", outcome="correct", rule_output="A")
        assert _is_correct(r, "A") is True

    def test_correct_outcome_mismatch_when_prediction_differs(self):
        r = _record(label="A", outcome="correct", rule_output="B")
        assert _is_correct(r, "B") is False

    def test_incorrect_outcome_credits_differing_predictions(self):
        # Convention from paper: chosen label was wrong; a shadow
        # that picked something other than the chosen label *might*
        # have been right, so we credit it.
        r = _record(label="A", outcome="incorrect", rule_output="B")
        assert _is_correct(r, "B") is True


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------


class TestRenderSwitchCard:
    def test_day_zero_card_is_valid_markdown(self, empty_storage):
        m = aggregate_switch(empty_storage, "triage_rule")
        out = render_switch_card(m)
        assert out.startswith("# Triage Rule — Graduation Report Card")
        assert "**Phase: `RULE`**" in out
        assert "0 outcomes recorded" in out
        assert "## Status" in out
        assert "## Phase timeline" in out
        # No charts paths supplied → placeholder text
        assert "Chart rendering pending" in out

    def test_graduated_card_shows_gate_fire(self, graduated_storage):
        m = aggregate_switch(
            graduated_storage, "test_switch", min_paired=10, alpha=0.05
        )
        out = render_switch_card(m, alpha=0.05)
        assert "graduated at outcome" in out
        assert "← gate" in out  # the gate-fire row is bolded in checkpoint table

    def test_humanizes_switch_name_in_title(self):
        s = InMemoryStorage()
        m = aggregate_switch(s, "output_safety_rule")
        out = render_switch_card(m)
        assert "# Output Safety Rule — Graduation Report Card" in out

    def test_includes_site_location_when_supplied(self, empty_storage):
        m = aggregate_switch(empty_storage, "x")
        out = render_switch_card(
            m, file_location="src/triage.py", site_function="triage_rule"
        )
        assert "Site: `src/triage.py:triage_rule`." in out

    def test_includes_fingerprint_when_supplied(self, empty_storage):
        m = aggregate_switch(empty_storage, "x", site_fingerprint="abc123def456")
        out = render_switch_card(m)
        assert "Fingerprint: `abc123def456`." in out

    def test_cost_section_only_when_cost_supplied(self, empty_storage):
        m = aggregate_switch(empty_storage, "x")
        # No cost supplied → no cost section
        out_no_cost = render_switch_card(m)
        assert "## Cost trajectory" not in out_no_cost
        # Cost supplied → cost section appears
        out_with_cost = render_switch_card(
            m, cost_per_call=0.0042, estimated_calls_per_month=1_000_000
        )
        assert "## Cost trajectory" in out_with_cost
        assert "$0.004200" in out_with_cost

    def test_hypothesis_section_renders_when_supplied(self, graduated_storage):
        m = aggregate_switch(graduated_storage, "test_switch", min_paired=10, alpha=0.05)
        verdict = HypothesisVerdict(
            predicted_graduation_low=50,
            predicted_graduation_high=150,
            predicted_effect_size_pp=5.0,
            observed_graduation_outcome=m.gate_fire_outcome,
            observed_effect_size_pp=20.0,
            observed_p_at_first_clear=m.gate_fire_p_value,
        )
        out = render_switch_card(m, hypothesis=verdict, alpha=0.05)
        assert "## Hypothesis evidence" in out
        assert "✓ Within interval" in out
        assert "✓ Met" in out

    def test_methodology_link_in_footer(self, empty_storage):
        m = aggregate_switch(empty_storage, "x")
        out = render_switch_card(m)
        assert "Test-Driven Product Development" in out

    def test_chart_paths_inserted_when_supplied(self, empty_storage):
        m = aggregate_switch(empty_storage, "x")
        out = render_switch_card(
            m,
            transition_chart_path="x.transition.png",
            pvalue_chart_path="x.pvalue.png",
        )
        assert "![Rule vs ML accuracy over outcomes](x.transition.png)" in out
        assert "![Gate p-value over outcomes](x.pvalue.png)" in out
        assert "Chart rendering pending" not in out

    def test_gate_fire_row_bolded_in_table(self, graduated_storage):
        m = aggregate_switch(graduated_storage, "test_switch", min_paired=10, alpha=0.05)
        out = render_switch_card(m, alpha=0.05)
        # The gate-fire row should be bold in the table
        gate_outcome = m.gate_fire_outcome
        assert gate_outcome is not None
        assert f"| **{gate_outcome}** |" in out
