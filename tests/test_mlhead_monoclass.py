# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for the single-class ML-head degenerate path.

Before this fix, ``SklearnTextHead.fit()`` (and the shared
``_fit_tfidf_head`` used by every TF-IDF head) bailed out silently when
training data collapsed to a single label, leaving ``_pipeline = None``.
The harness still reported ``ml_trained=True`` based on the
training-outcome count, so the head's ``predict()`` returned an empty
``label=""`` for every test row and the benchmark recorded 0%
ML accuracy.

This surfaced on the Snips benchmark in the v1.0 launch paper: the
HuggingFace ``benayas/snips`` mirror sorts the training split by label,
so outcomes 1 through ~1,842 are all ``AddToPlaylist``. The bench
harness fed those to the ML head, ``fit`` no-op'd, and Table 3's
``Snips ML @ 1k`` cell read 0.0% — implausible given a 14.3% rule
baseline on the same test split.

The semantic fix: a head that has only seen one label predicts that
label. ``_MonoclassPredictor`` is the constant predictor; ``fit()``
installs it as ``_pipeline`` and stamps ``model_version()`` with a
``-monoclass`` suffix so downstream telemetry can tell the difference.
"""

from __future__ import annotations

import pytest


class _FakeRec:
    """Minimal duck-typed record for the head's ``fit`` path."""

    def __init__(self, input: str, label: str, outcome: str = "correct") -> None:
        self.input = input
        self.label = label
        self.outcome = outcome


@pytest.fixture
def _require_sklearn():
    pytest.importorskip("sklearn")


class TestSklearnTextHeadMonoclass:
    """``SklearnTextHead`` trains a constant predictor on single-class data."""

    def test_fit_with_one_label_trains_monoclass_predictor(self, _require_sklearn):
        from dendra.ml import SklearnTextHead

        head = SklearnTextHead(min_outcomes=10)
        records = [_FakeRec(f"sample text {i}", "AddToPlaylist") for i in range(50)]
        head.fit(records)

        assert head._pipeline is not None, (
            "head should have trained a monoclass predictor instead of bailing out"
        )
        assert head.model_version().endswith("-monoclass"), (
            f"version string should mark the degenerate case; got {head.model_version()!r}"
        )

    def test_predict_after_monoclass_fit_returns_seen_label(self, _require_sklearn):
        from dendra.ml import SklearnTextHead

        head = SklearnTextHead(min_outcomes=10)
        records = [_FakeRec(f"sample {i}", "AddToPlaylist") for i in range(50)]
        head.fit(records)

        # Predictions on novel inputs all return the single observed label.
        for text in ["completely unrelated text", "another input", "yet another"]:
            pred = head.predict(text, ["AddToPlaylist", "BookRestaurant"])
            assert pred.label == "AddToPlaylist", (
                f"monoclass head should return the only seen label; got {pred.label!r}"
            )
            assert pred.confidence == 1.0

    def test_two_label_fit_uses_logreg_not_monoclass(self, _require_sklearn):
        """Regression guard: the monoclass branch must not steal multi-class fits."""
        from dendra.ml import SklearnTextHead

        head = SklearnTextHead(min_outcomes=10)
        records = [_FakeRec(f"crash report {i}", "bug") for i in range(30)] + [
            _FakeRec(f"feature request {i}", "feature") for i in range(30)
        ]
        head.fit(records)

        assert head._pipeline is not None
        assert not head.model_version().endswith("-monoclass"), (
            f"two-class fit should NOT land in the monoclass branch; got {head.model_version()!r}"
        )

    def test_skipped_below_min_outcomes_stays_untrained(self, _require_sklearn):
        from dendra.ml import SklearnTextHead

        head = SklearnTextHead(min_outcomes=100)
        records = [_FakeRec(f"sample {i}", "AddToPlaylist") for i in range(50)]
        head.fit(records)

        assert head._pipeline is None
        assert head.model_version() == "sklearn-untrained"

    def test_no_correct_records_stays_untrained(self, _require_sklearn):
        from dendra.ml import SklearnTextHead

        head = SklearnTextHead(min_outcomes=10)
        # All records are 'incorrect' -> filtered out -> y is empty
        records = [_FakeRec(f"sample {i}", "AddToPlaylist", outcome="incorrect") for i in range(50)]
        head.fit(records)

        assert head._pipeline is None
        assert head.model_version() == "sklearn-untrained"


class TestTfidfHeadsMonoclass:
    """The shared ``_fit_tfidf_head`` path covers every TF-IDF subclass."""

    def test_tfidf_linearsvc_monoclass_fit_and_predict(self, _require_sklearn):
        from dendra.ml import TfidfLinearSVCHead

        head = TfidfLinearSVCHead(min_outcomes=10)
        records = [_FakeRec(f"sample {i}", "AddToPlaylist") for i in range(50)]
        head.fit(records)

        assert head._pipeline is not None
        assert head.model_version().endswith("-monoclass")
        pred = head.predict("novel input", ["AddToPlaylist", "BookRestaurant"])
        assert pred.label == "AddToPlaylist"
        assert pred.confidence == 1.0

    def test_tfidf_multinomial_nb_monoclass_fit_and_predict(self, _require_sklearn):
        from dendra.ml import TfidfMultinomialNBHead

        head = TfidfMultinomialNBHead(min_outcomes=10)
        records = [_FakeRec(f"sample {i}", "OnlyClass") for i in range(50)]
        head.fit(records)

        assert head._pipeline is not None
        assert head.model_version().endswith("-monoclass")
        pred = head.predict("novel", ["OnlyClass", "OtherClass"])
        assert pred.label == "OnlyClass"
