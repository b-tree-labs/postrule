# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Configurable MLHead selection strategies.

Different MLHead choices win on different data shapes (see paper
§5.5 autoresearch findings). Rather than ship a single hard-coded
default, the system exposes the choice as a pluggable strategy:

- :class:`CardinalityMLHeadStrategy` is the shipped default. It
  picks based on the autoresearch rule (cardinality + samples-per-
  class). Thresholds are overridable.
- :class:`FixedMLHeadStrategy` always returns the head it was
  constructed with. Useful when an organization has run their own
  ``CandidateHarness`` and decided.
- :class:`MLHeadStrategy` is the duck-typed Protocol any custom
  strategy can implement.

The strategy itself is a Postrule rule applied to Postrule: a
hand-written rule today, with a clear graduation path (rule →
ML head trained on the autoresearch trajectory) in v1.x.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any, Protocol, runtime_checkable

from postrule.ml import (
    MLHead,
    SklearnTextHead,
    TfidfLinearSVCHead,
    TfidfMultinomialNBHead,
)


@runtime_checkable
class MLHeadStrategy(Protocol):
    """Pluggable MLHead-selection contract.

    Implementations take a sample of outcome records and return an
    MLHead instance suited to the data's shape. The strategy is
    consulted lazily on first need so it can observe the verdict log.
    """

    def select(self, records: Iterable[Any], /) -> MLHead: ...


class CardinalityMLHeadStrategy:
    """Default. Picks MLHead by label cardinality + samples-per-class.

    Decision rule (informed by paper §5.5):

    - cardinality ≥ ``high_cardinality_threshold`` AND
      samples-per-class < ``samples_per_class_threshold`` →
      :class:`TfidfMultinomialNBHead`. Naive Bayes' prior dominates
      on high-cardinality / low-density label spaces (CLINC150-shape).

    - ``mid_cardinality_threshold`` ≤ cardinality < high cutoff →
      :class:`TfidfLinearSVCHead`. Linear SVMs win on mid-cardinality
      with reasonable per-class density (ATIS / HWU64 / Banking77).

    - cardinality < ``mid_cardinality_threshold`` →
      :class:`SklearnTextHead` (logistic regression incumbent). Tight
      margins at low cardinality give no challenger statistical
      space to clear the gate (Snips).

    All thresholds are overridable so organizations with different
    empirical findings can configure their own crossover points.
    """

    __slots__ = (
        "high_cardinality_threshold",
        "mid_cardinality_threshold",
        "samples_per_class_threshold",
    )

    def __init__(
        self,
        *,
        high_cardinality_threshold: int = 100,
        mid_cardinality_threshold: int = 20,
        samples_per_class_threshold: int = 100,
    ) -> None:
        if high_cardinality_threshold <= mid_cardinality_threshold:
            raise ValueError(
                f"high_cardinality_threshold ({high_cardinality_threshold}) "
                f"must exceed mid_cardinality_threshold ({mid_cardinality_threshold})"
            )
        self.high_cardinality_threshold = high_cardinality_threshold
        self.mid_cardinality_threshold = mid_cardinality_threshold
        self.samples_per_class_threshold = samples_per_class_threshold

    def select(self, records: Iterable[Any]) -> MLHead:
        records = list(records)
        labels = [getattr(r, "label", None) for r in records]
        labels = [lbl for lbl in labels if lbl is not None]
        if not labels:
            return SklearnTextHead()
        counts = Counter(labels)
        n_labels = len(counts)
        avg_samples_per_class = sum(counts.values()) / n_labels

        if (
            n_labels >= self.high_cardinality_threshold
            and avg_samples_per_class < self.samples_per_class_threshold
        ):
            return TfidfMultinomialNBHead()
        if n_labels >= self.mid_cardinality_threshold:
            return TfidfLinearSVCHead()
        return SklearnTextHead()


class FixedMLHeadStrategy:
    """Always returns a single pre-constructed MLHead.

    Useful when an organization has already picked a winner
    empirically (e.g., via their own ``CandidateHarness`` run) and
    wants the switch to use that head regardless of the verdict log
    profile.
    """

    __slots__ = ("_head",)

    def __init__(self, head: MLHead) -> None:
        self._head = head

    def select(self, records: Iterable[Any]) -> MLHead:
        return self._head


__all__ = [
    "CardinalityMLHeadStrategy",
    "FixedMLHeadStrategy",
    "MLHeadStrategy",
]
