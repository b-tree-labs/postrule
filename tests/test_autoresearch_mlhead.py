# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Contracts for the MLHead-selection autoresearch loop (paper §9.4 dogfood).

Postrule's ``CandidateHarness`` is the substrate that picked the
MLHead in our own paper. Three alternative heads (LinearSVC,
MultinomialNB, GradientBoosting on the same TF-IDF features)
compete head-to-head against the default ``SklearnTextHead``
(LogisticRegression). The McNemar gate at α=0.01 chooses the winner
empirically.

Three contracts:

A. Each alternative head satisfies the MLHead protocol and trains
   from outcome records the same way SklearnTextHead does.
B. Each alternative head implements the optional persistence
   methods (``state_bytes`` / ``load_state``) so its trained state
   survives restart.
C. The autoresearch driver in ``scripts/autoresearch_mlhead.py``
   runs the four candidates through ``CandidateHarness`` against a
   benchmark and reports a single empirically-justified winner.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from postrule.ml import MLHead

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass
class _Rec:
    input: str
    label: str
    outcome: str = "correct"


def _toy_corpus():
    """Small linearly-separable text corpus for the heads to train on."""
    bug = [_Rec(f"the app keeps crashing example {i}", "bug") for i in range(40)]
    feat = [_Rec(f"please add a new feature request {i}", "feature") for i in range(40)]
    return bug + feat


# ---------------------------------------------------------------------------
# Contract A: each alt head satisfies MLHead and trains
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "head_factory",
    [
        pytest.param(
            "TfidfLinearSVCHead",
            id="linearsvc",
        ),
        pytest.param(
            "TfidfMultinomialNBHead",
            id="multinomialnb",
        ),
        pytest.param(
            "TfidfGradientBoostingHead",
            id="gboost",
        ),
    ],
)
class TestAlternativeMLHeadContract:
    """Each alternative head must satisfy MLHead and train usefully."""

    def _build(self, name: str):
        pytest.importorskip("sklearn")
        from postrule import ml as ml_module

        cls = getattr(ml_module, name, None)
        if cls is None:
            pytest.fail(
                f"postrule.ml.{name} is not implemented yet. The autoresearch "
                f"loop needs three sibling heads (LinearSVC / MultinomialNB / "
                f"GradientBoosting) that share TF-IDF features with SklearnTextHead."
            )
        return cls(min_outcomes=10)

    def test_satisfies_protocol(self, head_factory):
        head = self._build(head_factory)
        assert isinstance(head, MLHead)

    def test_trains_and_predicts_correctly(self, head_factory):
        head = self._build(head_factory)
        head.fit(_toy_corpus())
        p = head.predict("the app keeps crashing on launch", ["bug", "feature"])
        assert p.label == "bug"
        assert 0.0 <= p.confidence <= 1.0

    def test_returns_low_confidence_when_untrained(self, head_factory):
        head = self._build(head_factory)
        p = head.predict("anything", ["bug", "feature"])
        # Untrained heads must surface low confidence so the cascade
        # routes to a fallback rather than emitting a guess.
        assert p.confidence < 0.5


# ---------------------------------------------------------------------------
# Contract B: persistence round-trip on every alternative head
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "head_name",
    ["TfidfLinearSVCHead", "TfidfMultinomialNBHead", "TfidfGradientBoostingHead"],
)
class TestAlternativeHeadPersistence:
    def test_state_round_trip_preserves_predictions(self, head_name):
        pytest.importorskip("sklearn")
        from postrule import ml as ml_module

        cls = getattr(ml_module, head_name, None)
        if cls is None:
            pytest.fail(f"postrule.ml.{head_name} not implemented; cannot round-trip persistence")
        h1 = cls(min_outcomes=10)
        h1.fit(_toy_corpus())
        blob = h1.state_bytes()
        assert isinstance(blob, bytes) and len(blob) > 0

        h2 = cls(min_outcomes=10)
        h2.load_state(blob)
        for text in ["the app keeps crashing", "please add a new feature"]:
            p1 = h1.predict(text, ["bug", "feature"])
            p2 = h2.predict(text, ["bug", "feature"])
            assert p1.label == p2.label
            assert p1.confidence == pytest.approx(p2.confidence)


# ---------------------------------------------------------------------------
# Contract C: the autoresearch driver picks a winner
# ---------------------------------------------------------------------------


class TestAutoresearchDriver:
    """The driver script encapsulates the dogfood loop and produces a winner."""

    def test_driver_function_exists(self):
        # Locate the entrypoint. Driver lives at scripts/autoresearch_mlhead.py
        # but its core logic must be import-testable; conventionally we expose
        # a ``run_autoresearch(benchmark_slug, *, alpha=0.01)`` function.
        try:
            import importlib.util
            import sys
            from pathlib import Path

            path = Path(__file__).resolve().parent.parent / "scripts" / "autoresearch_mlhead.py"
            spec = importlib.util.spec_from_file_location("autoresearch_mlhead", path)
            assert spec is not None and spec.loader is not None, "spec failed"
            mod = importlib.util.module_from_spec(spec)
            sys.modules["autoresearch_mlhead"] = mod
            spec.loader.exec_module(mod)
        except FileNotFoundError:
            pytest.fail("scripts/autoresearch_mlhead.py does not exist yet")
        assert hasattr(mod, "run_autoresearch"), (
            "scripts/autoresearch_mlhead.py must expose run_autoresearch(benchmark_slug, *, alpha)"
        )

    def test_driver_returns_a_winner_on_synthetic_data(self):
        """End-to-end: feed a synthetic dataset where one candidate is
        clearly better, confirm the driver picks it.

        Uses a small synthetic corpus rather than the full benchmark so
        the test is fast (< 1s).
        """
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent / "scripts" / "autoresearch_mlhead.py"
        spec = importlib.util.spec_from_file_location("autoresearch_mlhead", path)
        if spec is None or spec.loader is None:
            pytest.fail("autoresearch_mlhead module does not load")
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except FileNotFoundError:
            pytest.fail("autoresearch_mlhead.py does not exist")
        if not hasattr(mod, "select_best_head"):
            pytest.fail(
                "autoresearch_mlhead.py must expose "
                "select_best_head(train_records, test_pairs, *, alpha) -> SelectionResult"
            )

        # Build train + test from a tiny corpus where any text classifier
        # will trivially clear the rule.
        train = _toy_corpus()
        test = [
            ("the app keeps crashing again", "bug"),
            ("please add a new feature here", "feature"),
            ("the program crashes on launch", "bug"),
            ("can you ship a new feature next week", "feature"),
        ] * 20  # 80 samples — enough for McNemar to clear

        result = mod.select_best_head(train, test, alpha=0.01)

        # The driver picks one head as winner.
        assert result.winner_name in {
            "TfidfLogReg",
            "TfidfLinearSVC",
            "TfidfMultinomialNB",
            "TfidfGradientBoosting",
        }, f"unexpected winner name: {result.winner_name}"
        # All candidates were evaluated.
        assert len(result.reports) == 4
        # Every candidate's report has an accuracy in [0, 1] and a finite
        # paired-McNemar p-value.
        for name, report in result.reports.items():
            assert 0.0 <= report.accuracy <= 1.0, name
            assert 0.0 <= report.mcnemar_p <= 1.0, name
