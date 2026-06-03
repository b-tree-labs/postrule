# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Adaptive Graduated Autonomy (AGA) — the improved phase-gate algorithm.

The shipped lifecycle (``core.Phase``) is a *fixed linear ladder*:
``RULE → MODEL_SHADOW/PRIMARY (LLM) → ML_SHADOW/.../ML_PRIMARY``, advanced one
rung at a time by the paired-McNemar gate, with ``ML_PRIMARY`` as the assumed
terminal. AGA keeps the gate and the phase space but makes the *path adaptive*:

  1. Choose the **terminal tier per stream** instead of always climbing to
     ML_PRIMARY. When traditional ML never reliably beats the LLM within the
     outcome budget (e.g. small-cardinality streams whose rule is near chance),
     the right terminal is ``MODEL_PRIMARY`` — stay on the LLM.
  2. **Cost-aware composition.** Among tiers whose accuracy is within ``epsilon``
     of the best, prefer the cheapest to *operate*: a rule and a trained NN/ML
     head cost ~nothing per call, whereas the LLM bills every inference forever.
     So the LLM is selected only when it *strictly* earns it (>epsilon better) —
     "too valuable to remove, too expensive to keep everywhere."
  3. **Crossover-aware advancement.** Don't pay to train ML / advance into the
     ML_* phases when the predicted ML→LLM crossover lies beyond the budget.
  4. A learned, continually-refined **meta-policy** predicts (1)-(3) from a
     stream's early-observable characteristics; this module provides both the
     oracle objective (the training target) and the policy wrapper.

``oracle_terminal`` is the supervised target computed from *measured* tier
accuracies; ``AGAMetaPolicy`` learns to predict it from characteristics alone,
so it generalizes to an unseen stream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from postrule.core import Phase


class Tier(str, Enum):
    """Decision-maker tiers, mapped onto core.Phase terminal states."""

    RULE = "rule"
    ML = "ml"  # traditional/classical ML → ML_PRIMARY
    MODEL_FEWSHOT = "model_fewshot"  # LLM (neural net) → MODEL_PRIMARY, k-shot
    MODEL_ZEROSHOT = "model_zeroshot"  # LLM (neural net) → MODEL_PRIMARY, 0-shot


# Per-call operating cost rank (lower = cheaper to run in steady state).
# Rule and trained ML are ~free per call; the LLM bills every inference.
_OPERATING_RANK = {
    Tier.RULE: 0,
    Tier.ML: 1,
    Tier.MODEL_FEWSHOT: 2,
    Tier.MODEL_ZEROSHOT: 2,
}

_TERMINAL_PHASE = {
    Tier.RULE: Phase.RULE,
    Tier.ML: Phase.ML_PRIMARY,
    Tier.MODEL_FEWSHOT: Phase.MODEL_PRIMARY,
    Tier.MODEL_ZEROSHOT: Phase.MODEL_PRIMARY,
}


@dataclass
class TierResult:
    """Measured accuracy + labeled-outcome cost for one tier on a stream."""

    tier: Tier
    accuracy: float
    labeled_outcomes: int  # x-position on the cost axis


@dataclass
class StreamCharacteristics:
    """Early-observable features used to choose composition for a fresh stream."""

    n_labels: int
    rule_baseline: float  # accuracy of the day-zero rule
    modality: str = "text"
    mean_input_len: float = 0.0
    class_balance: float = 1.0  # min/maj class ratio in [0,1]

    @property
    def rule_over_chance(self) -> float:
        chance = 1.0 / max(self.n_labels, 2)
        return self.rule_baseline / chance


def oracle_terminal(tiers: list[TierResult], *, epsilon: float = 0.02) -> Tier:
    """The cost-aware best terminal tier given *measured* accuracies.

    Pick the highest-accuracy tier; then, among all tiers within ``epsilon`` of
    it, prefer the one cheapest to operate (lowest operating rank), breaking
    remaining ties by higher accuracy then fewer labeled outcomes. This is the
    composition rule: the LLM wins only when it is strictly (>epsilon) better.
    """
    if not tiers:
        raise ValueError("no tiers to choose from")
    best_acc = max(t.accuracy for t in tiers)
    band = [t for t in tiers if t.accuracy >= best_acc - epsilon]
    band.sort(key=lambda t: (_OPERATING_RANK[t.tier], -t.accuracy, t.labeled_outcomes))
    return band[0].tier


def crossover_outcomes(ml_curve: list[tuple[int, float]], model_acc: float) -> int | None:
    """First training-outcome count where the ML curve overtakes the LLM line.

    Returns None if ML never reaches ``model_acc`` within the measured curve —
    the signal that AGA should *not* advance into the ML phases (stay on MODEL).
    """
    for n, acc in sorted(ml_curve):
        if acc >= model_acc:
            return n
    return None


def terminal_phase(tier: Tier) -> Phase:
    """Map an AGA tier choice to the concrete core.Phase terminal state."""
    return _TERMINAL_PHASE[tier]


@dataclass
class AGAMetaPolicy:
    """Learned policy: predict the oracle terminal tier from characteristics.

    Continually refined — call :meth:`fit` with accumulated (characteristics,
    oracle_tier) rows from every stream seen so far. Falls back to an
    interpretable heuristic when unfitted or sklearn is unavailable.
    """

    epsilon: float = 0.02
    _clf: object | None = field(default=None, repr=False)
    _feat_order: list[str] = field(default_factory=list, repr=False)
    _modalities: list[str] = field(default_factory=list, repr=False)

    @staticmethod
    def _features(c: StreamCharacteristics, modalities: list[str]) -> list[float]:
        import math

        base = [
            math.log(max(c.n_labels, 1)),
            c.rule_baseline,
            c.rule_over_chance,
            c.mean_input_len,
            c.class_balance,
        ]
        base += [1.0 if c.modality == m else 0.0 for m in modalities]
        return base

    def fit(self, rows: list[tuple[StreamCharacteristics, Tier]]) -> AGAMetaPolicy:
        self._modalities = sorted({c.modality for c, _ in rows})
        try:
            import numpy as np
            from sklearn.ensemble import GradientBoostingClassifier
        except ImportError:
            self._clf = None
            return self
        X = np.array([self._features(c, self._modalities) for c, _ in rows], dtype=float)
        y = np.array([t.value for _, t in rows])
        if len(set(y)) < 2:
            self._clf = None  # nothing to learn yet; heuristic will serve
            return self
        clf = GradientBoostingClassifier(random_state=0)
        clf.fit(X, y)
        self._clf = clf
        return self

    def recommend(self, c: StreamCharacteristics) -> Tier:
        if self._clf is not None:
            import numpy as np

            x = np.array([self._features(c, self._modalities)], dtype=float)
            return Tier(self._clf.predict(x)[0])
        return self._heuristic(c)

    def _heuristic(self, c: StreamCharacteristics) -> Tier:
        """Interpretable fallback distilled from the study's findings.

        High-cardinality streams whose rule is near chance graduate to ML
        quickly and ML tends to dominate → ML. Low-cardinality streams with a
        non-trivial rule baseline are where classical ML struggles to beat a
        strong LLM → MODEL_FEWSHOT. A rule already near-optimal stays RULE.
        """
        if c.rule_over_chance >= 1.6:
            return Tier.RULE
        if c.n_labels >= 10 and c.rule_over_chance <= 1.2:
            return Tier.ML
        return Tier.MODEL_FEWSHOT


__all__ = [
    "AGAMetaPolicy",
    "StreamCharacteristics",
    "Tier",
    "TierResult",
    "crossover_outcomes",
    "oracle_terminal",
    "terminal_phase",
]
