# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for dendra.viz — JSONL parsing + crossover computation.

matplotlib rendering is smoke-tested; heavy visual diffing is
out-of-scope.
"""

from __future__ import annotations

import json

import pytest

from dendra.viz import BenchmarkRun, load_run, plot_transition_curves


def _write_jsonl(path, summary, checkpoints):
    with open(path, "w") as f:
        f.write(json.dumps({"kind": "summary", **summary}) + "\n")
        for c in checkpoints:
            f.write(json.dumps({"kind": "checkpoint", **c}) + "\n")


class TestLoadRun:
    def test_parses_summary_and_checkpoints(self, tmp_path):
        path = tmp_path / "run.jsonl"
        _write_jsonl(
            path,
            {
                "benchmark": "atis",
                "labels": 26,
                "train_rows": 4978,
                "test_rows": 893,
                "seed_size": 100,
            },
            [
                {
                    "training_outcomes": 500,
                    "rule_test_accuracy": 0.7,
                    "ml_test_accuracy": 0.79,
                    "ml_trained": True,
                    "ml_version": "v1",
                },
                {
                    "training_outcomes": 1000,
                    "rule_test_accuracy": 0.7,
                    "ml_test_accuracy": 0.82,
                    "ml_trained": True,
                    "ml_version": "v2",
                },
            ],
        )
        run = load_run(path)
        assert isinstance(run, BenchmarkRun)
        assert run.benchmark == "atis"
        assert run.labels == 26
        assert run.outcomes() == [500, 1000]
        assert run.rule_accs() == [0.7, 0.7]
        assert run.ml_accs() == [0.79, 0.82]

    def test_missing_summary_raises(self, tmp_path):
        path = tmp_path / "run.jsonl"
        path.write_text("")
        with pytest.raises(ValueError, match="summary"):
            load_run(path)


class TestCrossover:
    def test_first_crossover_outcome(self, tmp_path):
        path = tmp_path / "run.jsonl"
        _write_jsonl(
            path,
            {
                "benchmark": "x",
                "labels": 10,
                "train_rows": 1000,
                "test_rows": 100,
                "seed_size": 100,
            },
            [
                {"training_outcomes": 100, "rule_test_accuracy": 0.7,
                 "ml_test_accuracy": 0.5, "ml_trained": True, "ml_version": "v"},
                {"training_outcomes": 250, "rule_test_accuracy": 0.7,
                 "ml_test_accuracy": 0.71, "ml_trained": True, "ml_version": "v"},
                {"training_outcomes": 500, "rule_test_accuracy": 0.7,
                 "ml_test_accuracy": 0.85, "ml_trained": True, "ml_version": "v"},
            ],
        )
        run = load_run(path)
        assert run.crossover_outcomes() == 250

    def test_no_crossover_returns_none(self, tmp_path):
        path = tmp_path / "run.jsonl"
        _write_jsonl(
            path,
            {"benchmark": "x", "labels": 10, "train_rows": 1000,
             "test_rows": 100, "seed_size": 100},
            [
                {"training_outcomes": 100, "rule_test_accuracy": 0.9,
                 "ml_test_accuracy": 0.5, "ml_trained": True, "ml_version": "v"},
                {"training_outcomes": 200, "rule_test_accuracy": 0.9,
                 "ml_test_accuracy": 0.6, "ml_trained": True, "ml_version": "v"},
            ],
        )
        run = load_run(path)
        assert run.crossover_outcomes() is None


class TestTransitionDepth:
    def test_returns_first_statistically_significant_outcome(self, tmp_path):
        path = tmp_path / "run.jsonl"
        _write_jsonl(
            path,
            {"benchmark": "x", "labels": 10, "train_rows": 5000,
             "test_rows": 1000, "seed_size": 100},
            [
                # rule=0.70, ml=0.72 → ~z=1.0, p≈0.16; not significant
                {"training_outcomes": 500, "rule_test_accuracy": 0.70,
                 "ml_test_accuracy": 0.72, "ml_trained": True, "ml_version": "v"},
                # rule=0.70, ml=0.80 → ~z=5.0, p≈3e-7; significant
                {"training_outcomes": 1000, "rule_test_accuracy": 0.70,
                 "ml_test_accuracy": 0.80, "ml_trained": True, "ml_version": "v"},
            ],
        )
        run = load_run(path)
        depth = run.transition_depth(alpha=0.01)
        assert depth == 1000

    def test_skips_untrained_ml_checkpoints(self, tmp_path):
        path = tmp_path / "run.jsonl"
        _write_jsonl(
            path,
            {"benchmark": "x", "labels": 10, "train_rows": 5000,
             "test_rows": 1000, "seed_size": 100},
            [
                {"training_outcomes": 50, "rule_test_accuracy": 0.5,
                 "ml_test_accuracy": 0.9, "ml_trained": False, "ml_version": "untrained"},
                {"training_outcomes": 500, "rule_test_accuracy": 0.5,
                 "ml_test_accuracy": 0.9, "ml_trained": True, "ml_version": "v"},
            ],
        )
        run = load_run(path)
        assert run.transition_depth() == 500

    def test_none_when_never_beats(self, tmp_path):
        path = tmp_path / "run.jsonl"
        _write_jsonl(
            path,
            {"benchmark": "x", "labels": 10, "train_rows": 1000,
             "test_rows": 100, "seed_size": 100},
            [{"training_outcomes": 500, "rule_test_accuracy": 0.9,
              "ml_test_accuracy": 0.5, "ml_trained": True, "ml_version": "v"}],
        )
        run = load_run(path)
        assert run.transition_depth() is None


class TestMcNemar:
    def test_ml_strictly_better_yields_small_p(self):
        from dendra.viz import mcnemar_p

        # Rule wrong, ML right on 20 examples; rule right ML wrong on 1.
        # One-sided binomial p on b=20, n=21 → very small.
        rule = [False] * 20 + [True]
        ml = [True] * 20 + [False]
        p = mcnemar_p(rule, ml)
        assert p is not None and p < 1e-4

    def test_equal_performance_yields_large_p(self):
        from dendra.viz import mcnemar_p

        # 5 each direction; b=5, n=10 → p ~ 0.62.
        rule = [False, False, False, False, False, True, True, True, True, True]
        ml = [True, True, True, True, True, False, False, False, False, False]
        p = mcnemar_p(rule, ml)
        assert p is not None and 0.4 < p < 0.8

    def test_no_disagreements_returns_one(self):
        from dendra.viz import mcnemar_p

        rule = [True, False, True]
        ml = [True, False, True]
        assert mcnemar_p(rule, ml) == 1.0

    def test_mismatched_lengths_returns_none(self):
        from dendra.viz import mcnemar_p
        assert mcnemar_p([True], [True, False]) is None

    def test_paired_preferred_when_available(self, tmp_path):
        path = tmp_path / "run.jsonl"
        _write_jsonl(
            path,
            {"benchmark": "x", "labels": 10, "train_rows": 1000,
             "test_rows": 30, "seed_size": 100},
            [
                {
                    "training_outcomes": 500,
                    "rule_test_accuracy": 15 / 30,
                    "ml_test_accuracy": 25 / 30,
                    "ml_trained": True,
                    "ml_version": "v",
                    # 10 examples where ML gained, 0 where rule gained.
                    "rule_correct": [True] * 15 + [False] * 15,
                    "ml_correct":   [True] * 25 + [False] * 5,
                }
            ],
        )
        run = load_run(path)
        depth = run.transition_depth(alpha=0.01)
        assert depth == 500


class TestFinalGap:
    def test_reports_delta(self, tmp_path):
        path = tmp_path / "run.jsonl"
        _write_jsonl(
            path,
            {"benchmark": "x", "labels": 10, "train_rows": 1000,
             "test_rows": 100, "seed_size": 100},
            [
                {"training_outcomes": 500, "rule_test_accuracy": 0.70,
                 "ml_test_accuracy": 0.80, "ml_trained": True, "ml_version": "v"},
                {"training_outcomes": 1000, "rule_test_accuracy": 0.70,
                 "ml_test_accuracy": 0.88, "ml_trained": True, "ml_version": "v"},
            ],
        )
        run = load_run(path)
        assert abs(run.final_gap() - 0.18) < 1e-9


class TestPlotSmoke:
    def test_writes_file(self, tmp_path):
        pytest.importorskip("matplotlib")
        path = tmp_path / "run.jsonl"
        _write_jsonl(
            path,
            {"benchmark": "atis", "labels": 26, "train_rows": 4978,
             "test_rows": 893, "seed_size": 100},
            [
                {"training_outcomes": 500, "rule_test_accuracy": 0.7,
                 "ml_test_accuracy": 0.79, "ml_trained": True, "ml_version": "v"},
                {"training_outcomes": 1000, "rule_test_accuracy": 0.7,
                 "ml_test_accuracy": 0.82, "ml_trained": True, "ml_version": "v"},
            ],
        )
        out = tmp_path / "figure.png"
        plot_transition_curves([load_run(path)], output_path=out, title="test")
        assert out.exists()
        assert out.stat().st_size > 0
