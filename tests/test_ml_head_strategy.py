# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Configurable MLHead selection strategy.

Three contracts the implementation must hold:

A. ``MLHeadStrategy`` is a Protocol with a single ``select(records)``
   method that returns an MLHead.

B. The default strategy ``CardinalityMLHeadStrategy`` picks based on
   the rule informed by paper §5.5 autoresearch:
   - cardinality >= 100 and samples_per_class < 100 → MultinomialNB
   - 20 <= cardinality <= 100 → LinearSVC
   - cardinality < 20 → LogReg (incumbent default)

C. ``FixedMLHeadStrategy`` returns the head it was constructed with,
   regardless of the records passed.

D. The thresholds on ``CardinalityMLHeadStrategy`` are overridable so
   organizations with different empirical findings can configure
   their own crossover points.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest


@dataclass
class _Rec:
    input: str
    label: str
    outcome: str = "correct"


def _records_with_n_labels(n_labels: int, samples_per_class: int = 100) -> list[_Rec]:
    out = []
    for label_id in range(n_labels):
        for sample_id in range(samples_per_class):
            out.append(_Rec(input=f"label{label_id} sample{sample_id}", label=f"label{label_id}"))
    return out


# ---------------------------------------------------------------------------
# A: Protocol
# ---------------------------------------------------------------------------


class TestMLHeadStrategyProtocol:
    def test_protocol_is_importable(self):
        try:
            from postrule.ml_strategy import MLHeadStrategy  # noqa: F401
        except ImportError:
            pytest.fail("postrule.ml_strategy.MLHeadStrategy not implemented yet")

    def test_default_strategy_is_importable(self):
        try:
            from postrule.ml_strategy import CardinalityMLHeadStrategy  # noqa: F401
        except ImportError:
            pytest.fail("postrule.ml_strategy.CardinalityMLHeadStrategy not implemented yet")

    def test_fixed_strategy_is_importable(self):
        try:
            from postrule.ml_strategy import FixedMLHeadStrategy  # noqa: F401
        except ImportError:
            pytest.fail("postrule.ml_strategy.FixedMLHeadStrategy not implemented yet")


# ---------------------------------------------------------------------------
# B: CardinalityMLHeadStrategy default thresholds
# ---------------------------------------------------------------------------


class TestCardinalityStrategyDefaults:
    def test_high_cardinality_picks_multinomial_nb(self):
        pytest.importorskip("sklearn")
        from postrule.ml import TfidfMultinomialNBHead
        from postrule.ml_strategy import CardinalityMLHeadStrategy

        # 150 labels, 30 samples each — CLINC150 shape; NB should win
        records = _records_with_n_labels(150, samples_per_class=30)
        head = CardinalityMLHeadStrategy().select(records)
        assert isinstance(head, TfidfMultinomialNBHead), (
            f"high-cardinality + low-samples-per-class should pick "
            f"MultinomialNB; got {type(head).__name__}"
        )

    def test_mid_cardinality_picks_linear_svc(self):
        pytest.importorskip("sklearn")
        from postrule.ml import TfidfLinearSVCHead
        from postrule.ml_strategy import CardinalityMLHeadStrategy

        # 50 labels, 200 samples each — HWU64/Banking77 shape; LinearSVC
        records = _records_with_n_labels(50, samples_per_class=200)
        head = CardinalityMLHeadStrategy().select(records)
        assert isinstance(head, TfidfLinearSVCHead), (
            f"mid-cardinality should pick LinearSVC; got {type(head).__name__}"
        )

    def test_low_cardinality_picks_logreg(self):
        pytest.importorskip("sklearn")
        from postrule.ml import SklearnTextHead
        from postrule.ml_strategy import CardinalityMLHeadStrategy

        # 7 labels, 200 samples each — Snips shape; LogReg (incumbent) holds
        records = _records_with_n_labels(7, samples_per_class=200)
        head = CardinalityMLHeadStrategy().select(records)
        assert isinstance(head, SklearnTextHead), (
            f"low-cardinality should pick LogReg (SklearnTextHead); got {type(head).__name__}"
        )


# ---------------------------------------------------------------------------
# C: FixedMLHeadStrategy
# ---------------------------------------------------------------------------


class TestFixedStrategy:
    def test_returns_constructed_head(self):
        pytest.importorskip("sklearn")
        from postrule.ml import TfidfMultinomialNBHead
        from postrule.ml_strategy import FixedMLHeadStrategy

        head = TfidfMultinomialNBHead()
        strat = FixedMLHeadStrategy(head)
        # Records of any shape — strategy ignores them.
        assert strat.select(_records_with_n_labels(7)) is head
        assert strat.select(_records_with_n_labels(150)) is head


# ---------------------------------------------------------------------------
# D: Threshold overrides
# ---------------------------------------------------------------------------


class TestCardinalityStrategyThresholdOverrides:
    def test_custom_high_cardinality_threshold(self):
        pytest.importorskip("sklearn")
        from postrule.ml import TfidfMultinomialNBHead
        from postrule.ml_strategy import CardinalityMLHeadStrategy

        # Lower the high-cardinality threshold to 30, so a 50-label /
        # 30-samples-per-class dataset now triggers NB instead of
        # LinearSVC.
        strat = CardinalityMLHeadStrategy(
            high_cardinality_threshold=30,
            samples_per_class_threshold=100,
        )
        records = _records_with_n_labels(50, samples_per_class=30)
        head = strat.select(records)
        assert isinstance(head, TfidfMultinomialNBHead)

    def test_custom_mid_cardinality_threshold(self):
        pytest.importorskip("sklearn")
        from postrule.ml import SklearnTextHead, TfidfLinearSVCHead
        from postrule.ml_strategy import CardinalityMLHeadStrategy

        # Raise mid-cardinality minimum to 30. A 25-label dataset is
        # now below the cutoff and should fall to LogReg.
        strat = CardinalityMLHeadStrategy(mid_cardinality_threshold=30)
        head_low = strat.select(_records_with_n_labels(25, samples_per_class=200))
        assert isinstance(head_low, SklearnTextHead)

        # And a 50-label dataset is still mid-cardinality.
        head_mid = strat.select(_records_with_n_labels(50, samples_per_class=200))
        assert isinstance(head_mid, TfidfLinearSVCHead)


# ---------------------------------------------------------------------------
# E: Public exports
# ---------------------------------------------------------------------------


class TestPublicExports:
    def test_exported_from_postrule_top_level(self):
        import postrule

        for name in (
            "MLHeadStrategy",
            "CardinalityMLHeadStrategy",
            "FixedMLHeadStrategy",
        ):
            assert hasattr(postrule, name), (
                f"postrule.{name} must be exported at the top level for "
                f"easy use in switch construction"
            )
