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
    """Measured accuracy + the multi-objective axes for one tier on a stream.

    Tier selection is multi-objective: accuracy (within a significance band),
    latency (a hard SLO), modality feasibility (a hard filter), and operating
    cost (the soft objective to minimize). ``labeled_outcomes`` is the one-time
    label cost; ``per_call_ms``/``feasible`` carry the latency and modality axes.
    Cost is the dimension this study measured; the others are first-class in the
    policy and default to "unconstrained" so cost-only behavior is recovered.
    """

    tier: Tier
    accuracy: float
    labeled_outcomes: int  # one-time label cost (x-axis of the transition curve)
    per_call_ms: float | None = None  # steady-state latency (None = unknown/unconstrained)
    feasible: bool = True  # modality feasibility (e.g. spectrogram-vision can't read words)


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


def wilson_halfwidth(acc: float, n: int, z: float = 1.96) -> float:
    """Approximate Wilson 95% CI half-width for a proportion — used to treat
    sub-noise accuracy differences as ties rather than real wins."""
    if n <= 0:
        return 1.0
    import math

    return z * math.sqrt(max(acc * (1 - acc), 1e-9) / n) + z * z / (2 * n)


def oracle_terminal(
    tiers: list[TierResult],
    *,
    epsilon: float = 0.02,
    sample_n: int | None = None,
    latency_slo_ms: float | None = None,
) -> Tier:
    """The multi-objective best terminal tier given *measured* tier results.

    The decision is multi-objective, applied in priority order:
      1. **Modality feasibility** (hard): drop ``feasible=False`` tiers.
      2. **Latency SLO** (hard): if ``latency_slo_ms`` is set, drop tiers whose
         ``per_call_ms`` exceeds it.
      3. **Accuracy** (significance band): keep tiers within
         ``max(epsilon, wilson_halfwidth(best_acc, sample_n))`` of the best
         feasible accuracy, so sub-noise differences collapse to a tie (this is
         what stabilizes the oracle/meta-policy across re-runs).
      4. **Operating cost** (soft, minimized): among the band, prefer the cheapest
         to operate (lowest operating rank), then higher accuracy, then fewer
         labeled outcomes. The LLM wins only when it is *significantly* better.

    Cost is the dimension this study measured; latency and modality are
    first-class axes that default to unconstrained (recovering cost-only
    selection). If every tier is infeasible/over-SLO the constraints are relaxed
    to avoid an empty choice (a degraded-mode fallback the caller can detect).
    """
    if not tiers:
        raise ValueError("no tiers to choose from")
    candidates = [t for t in tiers if t.feasible]
    if latency_slo_ms is not None:
        candidates = [
            t for t in candidates if t.per_call_ms is None or t.per_call_ms <= latency_slo_ms
        ]
    if not candidates:  # degraded mode: no tier meets the hard constraints
        candidates = list(tiers)
    best_acc = max(t.accuracy for t in candidates)
    band_eps = epsilon
    if sample_n:
        band_eps = max(epsilon, wilson_halfwidth(best_acc, sample_n))
    band = [t for t in candidates if t.accuracy >= best_acc - band_eps]
    band.sort(key=lambda t: (_OPERATING_RANK[t.tier], -t.accuracy, t.labeled_outcomes))
    return band[0].tier


def crossover_outcomes(
    ml_curve: list[tuple[int, float]], model_acc: float, *, epsilon: float = 0.02
) -> int | None:
    """First training-outcome count where classical ML reaches a *consistent tie*
    with the LLM line — i.e. is no longer meaningfully worse, within ``epsilon``.

    ML does NOT have to *beat* the LLM to justify graduating to it: once ML ties
    (statistical non-inferiority), AGA advances to the ML phase to shed the LLM's
    perpetual per-call inference cost. So the bar is ``acc >= model_acc - epsilon``,
    not ``acc >= model_acc``. Returns None if ML never ties within the measured
    curve — the signal that AGA should stay on MODEL (the LLM is the terminal).

    Note: ``epsilon`` here is a point-estimate stand-in for the proper criterion —
    a sustained non-inferiority (TOST / equivalence) test on the paired stream,
    the equivalence-direction analog of the paper's McNemar superiority gate.
    """
    for n, acc in sorted(ml_curve):
        if acc >= model_acc - epsilon:
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
