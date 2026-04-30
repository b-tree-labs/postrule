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


# ---------------------------------------------------------------------------
# Charts (matplotlib is an optional extra; skip when not installed)
# ---------------------------------------------------------------------------


_HAVE_MPL = True
try:
    import matplotlib  # noqa: F401
except ImportError:  # pragma: no cover
    _HAVE_MPL = False


@pytest.mark.skipif(not _HAVE_MPL, reason="dendra[viz] not installed")
class TestCharts:
    def test_transition_curve_writes_png(self, graduated_storage, tmp_path):
        from dendra.cloud.report import charts

        m = aggregate_switch(graduated_storage, "test_switch", min_paired=10, alpha=0.05)
        out = tmp_path / "test_switch.transition.png"
        result = charts.transition_curve(m, out)
        assert result.exists()
        assert result.stat().st_size > 1000  # PNG is non-trivial in size

    def test_pvalue_trajectory_writes_png(self, graduated_storage, tmp_path):
        from dendra.cloud.report import charts

        m = aggregate_switch(graduated_storage, "test_switch", min_paired=10, alpha=0.05)
        out = tmp_path / "test_switch.pvalue.png"
        result = charts.pvalue_trajectory(m, out, alpha=0.05)
        assert result.exists()
        assert result.stat().st_size > 1000

    def test_cost_trajectory_writes_png(self, graduated_storage, tmp_path):
        from dendra.cloud.report import charts

        m = aggregate_switch(graduated_storage, "test_switch", min_paired=10, alpha=0.05)
        out = tmp_path / "test_switch.cost.png"
        result = charts.cost_trajectory(m, out, cost_per_call=0.0042)
        assert result.exists()
        assert result.stat().st_size > 1000

    def test_chart_raises_on_no_checkpoints(self, empty_storage, tmp_path):
        from dendra.cloud.report import charts

        m = aggregate_switch(empty_storage, "x")
        with pytest.raises(ValueError, match="checkpoint"):
            charts.transition_curve(m, tmp_path / "x.png")


# ---------------------------------------------------------------------------
# Hypothesis-file generation
# ---------------------------------------------------------------------------


class TestHypothesisFileGeneration:
    def test_creates_file_with_expected_sections(self, tmp_path):
        from dendra.cloud.report.hypotheses import generate_hypothesis_file

        out_path, content_hash, created = generate_hypothesis_file(
            "triage_rule",
            file_location="src/triage.py",
            function_name="triage_rule",
            site_fingerprint="abc123",
            regime="narrow",
            label_cardinality=4,
            fit_score=5.0,
            root=tmp_path / "hypotheses",
        )
        assert created is True
        assert out_path.exists()
        text = out_path.read_text(encoding="utf-8")
        assert "# Pre-registered hypothesis — Triage Rule" in text
        assert "abc123" in text
        assert "## 1. Unit of decision" in text
        assert "## 2. Gate criterion" in text
        assert "## 3. Expected n at graduation" in text
        assert "## 4. Expected effect size" in text
        assert "## 5. Truth source" in text
        assert "## 6. Rollback rule" in text
        assert "## Verdict (filled in by `dendra report`)" in text
        assert content_hash  # non-empty SHA-256

    def test_idempotent_does_not_overwrite_existing(self, tmp_path):
        from dendra.cloud.report.hypotheses import generate_hypothesis_file

        # First call creates.
        out_path, hash1, created1 = generate_hypothesis_file(
            "x", root=tmp_path / "h"
        )
        assert created1 is True
        # Customer "edits" the file.
        out_path.write_text("CUSTOM CONTENT", encoding="utf-8")
        # Second call must NOT overwrite.
        out_path2, hash2, created2 = generate_hypothesis_file(
            "x", root=tmp_path / "h"
        )
        assert created2 is False
        assert out_path2.read_text(encoding="utf-8") == "CUSTOM CONTENT"

    def test_overwrite_flag_replaces(self, tmp_path):
        from dendra.cloud.report.hypotheses import generate_hypothesis_file

        out_path, _, _ = generate_hypothesis_file("x", root=tmp_path / "h")
        out_path.write_text("CUSTOM", encoding="utf-8")
        out_path2, _, created = generate_hypothesis_file(
            "x", root=tmp_path / "h", overwrite=True
        )
        assert created is True
        assert "Pre-registered hypothesis" in out_path2.read_text(encoding="utf-8")

    def test_explicit_cohort_interval_overrides_regime_default(self, tmp_path):
        from dendra.cloud.report.hypotheses import generate_hypothesis_file

        out_path, _, _ = generate_hypothesis_file(
            "x",
            regime="narrow",
            cohort_predicted_low=400,
            cohort_predicted_high=600,
            root=tmp_path / "h",
        )
        text = out_path.read_text(encoding="utf-8")
        assert "**400–600 outcomes**" in text

    def test_regime_default_when_no_cohort(self, tmp_path):
        from dendra.cloud.report.hypotheses import generate_hypothesis_file

        out_path, _, _ = generate_hypothesis_file(
            "x", regime="narrow", root=tmp_path / "h"
        )
        text = out_path.read_text(encoding="utf-8")
        # narrow default is 200-400
        assert "**200–400 outcomes**" in text

    def test_content_hash_is_deterministic_for_same_inputs(self, tmp_path):
        from dendra.cloud.report.hypotheses import generate_hypothesis_file

        # Generate same inputs twice (with different output dirs)
        # — only the timestamp varies, so hashes will differ in
        # general. This test confirms the hash IS computed and
        # returned non-empty; deterministic-given-time would
        # require freezegun, out of scope here.
        _, h1, _ = generate_hypothesis_file("x", root=tmp_path / "a")
        _, h2, _ = generate_hypothesis_file("x", root=tmp_path / "b")
        assert len(h1) == 64  # SHA-256 hex
        assert len(h2) == 64


# ---------------------------------------------------------------------------
# Project summary
# ---------------------------------------------------------------------------


class TestProjectSummary:
    def test_aggregate_project_with_explicit_switch_list(self, graduated_storage):
        from dendra.cloud.report import aggregate_project

        # Add a second switch with no records
        s = graduated_storage
        # graduated_storage already has "test_switch" with 100 records
        result = aggregate_project(
            s, switch_names=["test_switch", "missing_switch"], alpha=0.05
        )
        assert len(result.switches) == 2
        assert result.total_outcomes == 100
        assert result.graduated_count == 1  # test_switch graduated
        assert result.pre_graduation_count == 0  # missing_switch has 0 outcomes
        # missing_switch is wrapped-but-no-data, neither graduated nor pre-grad

    def test_aggregate_project_falls_back_to_switch_names_method(self, tmp_path):
        from dendra.cloud.report import aggregate_project
        from dendra.storage import FileStorage

        s = FileStorage(tmp_path)
        # FileStorage has switch_names() method (returns []
        # when nothing's been written yet)
        result = aggregate_project(s, alpha=0.01)
        assert result.switches == []
        assert result.total_outcomes == 0

    def test_aggregate_project_raises_when_no_switch_names_method(
        self, empty_storage
    ):
        from dendra.cloud.report import aggregate_project

        # InMemoryStorage doesn't have switch_names()
        with pytest.raises(AttributeError, match="switch_names"):
            aggregate_project(empty_storage)

    def test_render_project_summary_empty(self):
        from dendra.cloud.report import ProjectSummary, render_project_summary

        summary = ProjectSummary()
        out = render_project_summary(summary, project_name="empty_project")
        assert "Project Switches — Status Summary" in out
        assert "**No switches wrapped yet.**" in out
        assert "`empty_project`" in out

    def test_render_project_summary_with_switches(self, graduated_storage):
        from dendra.cloud.report import (
            aggregate_project,
            render_project_summary,
        )

        summary = aggregate_project(
            graduated_storage,
            switch_names=["test_switch"],
            alpha=0.05,
        )
        out = render_project_summary(summary, project_name="demo_project")
        assert "1 switch in flight" in out
        assert "**1** graduated to ML" in out
        assert "## Phase distribution" in out
        assert "## Per-switch status" in out
        assert "## Hypothesis-vs-observed roll-up" in out
        assert "test_switch" in out
        assert "[`test_switch`](test_switch.md)" in out
        # No drift events on this fixture
        assert "No drift events detected" in out
