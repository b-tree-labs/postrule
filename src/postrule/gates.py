# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Phase-graduation gates.

A :class:`Gate` looks at a switch's accumulated outcome records and
decides whether the switch has earned its next phase. Graduation is
evidence-gated, not gut-feel-gated: the built-in
:class:`McNemarGate` rejects the null hypothesis that the higher-
tier decision-maker is no better than the current one at a
configured significance level.

Gates are swappable via :attr:`SwitchConfig.gate`; custom gates
that conform to the :class:`Gate` protocol can enforce domain-
specific thresholds (minimum accuracy, bounded regret, operator
approval, composite conditions).

See ``examples/07_llm_as_teacher.py`` for auto-graduation in
action.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from postrule.core import ClassificationRecord, Phase, Verdict

# Phase ordering — duplicated intentionally from core._PHASE_ORDER to
# avoid a cross-module private dependency. Gate code is licence-
# licence-compatible Apache; core is the same today but the
# duplication keeps this module self-contained for subclassing.
_PHASE_ORDER: dict[Phase, int] = {
    Phase.RULE: 0,
    Phase.MODEL_SHADOW: 1,
    Phase.MODEL_PRIMARY: 2,
    Phase.ML_SHADOW: 3,
    Phase.ML_WITH_FALLBACK: 4,
    Phase.ML_PRIMARY: 5,
}


def next_phase(current: Phase) -> Phase | None:
    """The next phase in the six-phase lifecycle, or ``None`` at the top."""
    order = _PHASE_ORDER[current]
    for phase, idx in _PHASE_ORDER.items():
        if idx == order + 1:
            return phase
    return None


def prev_phase(current: Phase) -> Phase | None:
    """The previous phase in the six-phase lifecycle, or ``None`` at the floor.

    The symmetric counterpart of :func:`next_phase`. Used by
    :meth:`LearnedSwitch.demote` and the auto-demote loop to walk
    the lifecycle backward when accumulated evidence shows the
    current phase's decision-maker has drifted below the rule's
    accuracy.
    """
    order = _PHASE_ORDER[current]
    for phase, idx in _PHASE_ORDER.items():
        if idx == order - 1:
            return phase
    return None


# ---------------------------------------------------------------------------
# GateDecision + Gate Protocol
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateDecision:
    """What a :class:`Gate` returns from ``evaluate``.

    ``target_better`` is the binding answer: was the ``target_phase``
    decision-maker reliably better than the ``current_phase``
    decision-maker on the paired correctness evidence, per the gate's
    test? Direction-agnostic: this flag carries no opinion about
    whether "better" means advance, demote, or sideways move. The
    caller (``LearnedSwitch.advance``, ``LearnedSwitch.demote``, or a
    future axis-step method) interprets it in domain context.

    ``rationale`` is the human-readable explanation surfaced in
    telemetry and audit logs. Statistical gates include ``p_value``
    and ``paired_sample_size`` so operators and auditors can replay
    the decision.
    """

    target_better: bool
    rationale: str
    p_value: float | None = None
    paired_sample_size: int = 0
    current_accuracy: float | None = None
    target_accuracy: float | None = None


@runtime_checkable
class Gate(Protocol):
    """Statistical comparator over two states.

    Takes records and two phases; answers whether the target's
    decision-maker is reliably better than the current's per the
    gate's test. The gate is direction-agnostic: it does not know
    whether "better" means advance, demote, or another axis-step.
    The caller interprets the result in domain context.

    Sibling concrete gates: :class:`McNemarGate`,
    :class:`AccuracyMarginGate`, :class:`MinVolumeGate`,
    :class:`CompositeGate`, :class:`ManualGate`. The gate does NOT
    mutate state; ``LearnedSwitch.advance`` and
    ``LearnedSwitch.demote`` actually update the phase when the gate
    says ``target_better``.
    """

    def evaluate(
        self,
        records: list[ClassificationRecord],
        current_phase: Phase,
        target_phase: Phase,
        /,
    ) -> GateDecision: ...


# ---------------------------------------------------------------------------
# Paired correctness extraction
# ---------------------------------------------------------------------------


def _source_correct_for(record: ClassificationRecord, source_field: str) -> bool | None:
    """Was the source's prediction the correct one for this record?

    Only deterministic when ``outcome == "correct"``:
        - ``source_field == output`` → source was right
        - ``source_field != output`` → source was wrong
    When ``outcome == "incorrect"``, we know the user-visible output
    was wrong but not what the correct label was; we can only say
    ``source_correct = False`` when ``source_field == output``
    (same wrong answer), else indeterminate. This matches the
    approximation used in the research / viz layer — only
    correct-outcome records contribute to McNemar pairs.
    """
    if record.outcome != Verdict.CORRECT.value:
        return None
    source_value = getattr(record, source_field, None)
    if source_value is None:
        return None
    return source_value == record.label


def _paired_correctness(
    records: list[ClassificationRecord],
    current_phase: Phase,
    target_phase: Phase,
) -> tuple[list[bool], list[bool]]:
    """Extract parallel (current_correct, target_correct) boolean lists.

    Returns only records where BOTH sources have observations —
    unpaired rows are dropped. Uses the "correct-outcome only"
    approximation described in :func:`_source_correct_for`.
    """
    current_field = _phase_source_field(current_phase)
    target_field = _phase_source_field(target_phase)
    if current_field is None or target_field is None:
        return [], []

    current_correct: list[bool] = []
    target_correct: list[bool] = []
    for r in records:
        c = _source_correct_for(r, current_field)
        t = _source_correct_for(r, target_field)
        if c is None or t is None:
            continue
        current_correct.append(c)
        target_correct.append(t)
    return current_correct, target_correct


def _phase_source_field(phase: Phase) -> str | None:
    """Which ``*_output`` field on the record holds this phase's prediction.

    - RULE / MODEL_SHADOW: the rule decides; rule_output is the source.
    - MODEL_PRIMARY / ML_SHADOW: the model decides; model_output is the source.
    - ML_WITH_FALLBACK / ML_PRIMARY: the ML head decides; ml_output is the source.
    """
    if phase in (Phase.RULE, Phase.MODEL_SHADOW):
        return "rule_output"
    if phase in (Phase.MODEL_PRIMARY, Phase.ML_SHADOW):
        return "model_output"
    if phase in (Phase.ML_WITH_FALLBACK, Phase.ML_PRIMARY):
        return "ml_output"
    return None


# ---------------------------------------------------------------------------
# McNemarGate — the default
# ---------------------------------------------------------------------------


DEFAULT_ALPHA = 0.01
DEFAULT_MIN_PAIRED = 200


class McNemarGate:
    """Paired-proportion test gate (McNemar's exact / normal-approx).

    Advances when the target-phase decision-maker is reliably better
    than the current-phase decision-maker at significance ``alpha``,
    with at least ``min_paired`` correct-outcome records containing
    predictions from BOTH sources.

    The underlying test is the two-sided exact binomial on discordant
    pairs as described in Algorithm 1 of the paper (``body.typ`` §3.2):
    ``p = min(1.0, 2 * BinomialCDF(min(b, c); b + c, 0.5))``. The
    advance condition pairs ``p < alpha`` with the directional check
    ``b > c`` (target wins more disagreement than current); the
    direction filter is implicit in the gate's caller-decided
    ``current_phase``/``target_phase`` orientation.

    Statistical contract: the probability of advancing to a worse-
    than-current phase is bounded above by ``alpha``. A false-negative
    (refuse to advance despite real improvement) is bounded by the
    test's Type-II error at the observed effect size.

    The "correct-outcome only" approximation means incorrect-outcome
    records are discarded — they contribute no paired-correctness
    information without the ground-truth label. For the McNemar
    comparison this is conservative (fewer discordant pairs → higher
    p → more reluctant to advance).
    """

    def __init__(
        self,
        alpha: float = DEFAULT_ALPHA,
        min_paired: int = DEFAULT_MIN_PAIRED,
    ) -> None:
        if not 0.0 < alpha < 1.0:
            raise ValueError(f"alpha must be in (0, 1); got {alpha}")
        if min_paired <= 0:
            raise ValueError(f"min_paired must be positive; got {min_paired}")
        self._alpha = alpha
        self._min_paired = min_paired

    @property
    def alpha(self) -> float:
        return self._alpha

    @property
    def min_paired(self) -> int:
        return self._min_paired

    def evaluate(
        self,
        records: list[ClassificationRecord],
        current_phase: Phase,
        target_phase: Phase,
        /,
    ) -> GateDecision:
        current_correct, target_correct = _paired_correctness(records, current_phase, target_phase)
        n = len(current_correct)

        if n < self._min_paired:
            return GateDecision(
                target_better=False,
                rationale=(f"insufficient paired samples: {n} < {self._min_paired} required"),
                paired_sample_size=n,
            )

        current_acc = sum(current_correct) / n
        target_acc = sum(target_correct) / n

        # Discordant pair direction (paper Algorithm 1, body.typ §3.2):
        # b is "target right, current wrong"; c is "current right, target wrong".
        # The two-sided p answers "are b and c imbalanced?"; the directional
        # filter b > c ensures we only advance when the imbalance points the
        # right way (target wins more disagreement than current).
        b = sum(
            1 for cur, tgt in zip(current_correct, target_correct, strict=True) if (not cur) and tgt
        )
        c = sum(
            1 for cur, tgt in zip(current_correct, target_correct, strict=True) if cur and (not tgt)
        )

        # Lazy import to avoid a hard dependency on viz for core.
        from postrule.viz import mcnemar_p

        p = mcnemar_p(current_correct, target_correct)
        if p is None:
            return GateDecision(
                target_better=False,
                rationale="mcnemar_p returned None (degenerate input)",
                paired_sample_size=n,
                current_accuracy=current_acc,
                target_accuracy=target_acc,
            )

        if p < self._alpha and b > c:
            return GateDecision(
                target_better=True,
                rationale=(
                    f"McNemar p={p:.4g} < alpha={self._alpha} (b={b} > c={c}); "
                    f"{current_phase.name}={current_acc:.1%} "
                    f"→ {target_phase.name}={target_acc:.1%} on {n} paired samples"
                ),
                p_value=p,
                paired_sample_size=n,
                current_accuracy=current_acc,
                target_accuracy=target_acc,
            )
        if p < self._alpha:
            # Significant imbalance but in the wrong direction: target loses
            # more than it wins. Refuse to advance.
            return GateDecision(
                target_better=False,
                rationale=(
                    f"McNemar p={p:.4g} < alpha={self._alpha} but b={b} <= c={c}; "
                    f"target does not beat current on {n} paired samples"
                ),
                p_value=p,
                paired_sample_size=n,
                current_accuracy=current_acc,
                target_accuracy=target_acc,
            )
        return GateDecision(
            target_better=False,
            rationale=(
                f"McNemar p={p:.4g} >= alpha={self._alpha}; "
                f"evidence insufficient on {n} paired samples"
            ),
            p_value=p,
            paired_sample_size=n,
            current_accuracy=current_acc,
            target_accuracy=target_acc,
        )


# ---------------------------------------------------------------------------
# ManualGate — never advances. For operator-controlled graduation.
# ---------------------------------------------------------------------------


class ManualGate:
    """Gate that never advances. Phase change is operator-driven.

    Use when regulatory or domain constraints require explicit human
    approval at every transition. ``LearnedSwitch.advance()`` will
    always return ``advance=False`` and the caller is expected to
    mutate ``config.starting_phase`` directly (or use an
    operator-workflow custom gate).
    """

    def evaluate(
        self,
        records: list[ClassificationRecord],  # noqa: ARG002
        current_phase: Phase,
        target_phase: Phase,  # noqa: ARG002
        /,
    ) -> GateDecision:
        return GateDecision(
            target_better=False,
            rationale=f"ManualGate: graduation from {current_phase.name} requires operator action",
        )


# ---------------------------------------------------------------------------
# AccuracyMarginGate — advance when target beats current by a margin
# ---------------------------------------------------------------------------


class AccuracyMarginGate:
    """Advance when target accuracy exceeds current by ``margin``.

    No significance test — just a point-estimate comparison. Useful
    when statistical power is ample (thousands of samples) or when
    paired-test assumptions don't apply.

    Refuses when the number of paired correct-outcome records is
    below ``min_paired``. Ties and reversals always refuse.
    """

    def __init__(self, margin: float = 0.05, min_paired: int = DEFAULT_MIN_PAIRED) -> None:
        if not 0.0 <= margin < 1.0:
            raise ValueError(f"margin must be in [0, 1); got {margin}")
        if min_paired <= 0:
            raise ValueError(f"min_paired must be positive; got {min_paired}")
        self._margin = margin
        self._min_paired = min_paired

    @property
    def margin(self) -> float:
        return self._margin

    @property
    def min_paired(self) -> int:
        return self._min_paired

    def evaluate(
        self,
        records: list[ClassificationRecord],
        current_phase: Phase,
        target_phase: Phase,
        /,
    ) -> GateDecision:
        current_correct, target_correct = _paired_correctness(records, current_phase, target_phase)
        n = len(current_correct)
        if n < self._min_paired:
            return GateDecision(
                target_better=False,
                rationale=(f"insufficient paired samples: {n} < {self._min_paired} required"),
                paired_sample_size=n,
            )
        current_acc = sum(current_correct) / n
        target_acc = sum(target_correct) / n
        delta = target_acc - current_acc
        if delta > self._margin:
            return GateDecision(
                target_better=True,
                rationale=(
                    f"accuracy delta {delta:+.1%} exceeds margin {self._margin:.1%} "
                    f"({current_phase.name}={current_acc:.1%} "
                    f"→ {target_phase.name}={target_acc:.1%} on {n} samples)"
                ),
                paired_sample_size=n,
                current_accuracy=current_acc,
                target_accuracy=target_acc,
            )
        return GateDecision(
            target_better=False,
            rationale=(
                f"accuracy delta {delta:+.1%} within margin {self._margin:.1%} on {n} samples"
            ),
            paired_sample_size=n,
            current_accuracy=current_acc,
            target_accuracy=target_acc,
        )


# ---------------------------------------------------------------------------
# MinVolumeGate — require N records before delegating
# ---------------------------------------------------------------------------


class MinVolumeGate:
    """Wrap another gate; refuse until ``min_records`` are logged.

    Useful as a composition primitive when the underlying statistical
    gate (McNemar, etc.) has its own min_paired threshold but the
    operator wants a stricter floor — e.g., "do not advance until we
    have at least 2,000 outcomes observed, regardless of paired
    significance." Counts all records (both verdict and UNKNOWN);
    delegates to the wrapped gate once the threshold is reached.
    """

    def __init__(self, inner: Gate, *, min_records: int) -> None:
        if min_records <= 0:
            raise ValueError(f"min_records must be positive; got {min_records}")
        self._inner = inner
        self._min_records = min_records

    @property
    def inner(self) -> Gate:
        return self._inner

    @property
    def min_records(self) -> int:
        return self._min_records

    def evaluate(
        self,
        records: list[ClassificationRecord],
        current_phase: Phase,
        target_phase: Phase,
        /,
    ) -> GateDecision:
        n = len(records)
        if n < self._min_records:
            return GateDecision(
                target_better=False,
                rationale=(
                    f"MinVolumeGate: {n} records < {self._min_records} required "
                    "before delegating to inner gate"
                ),
                paired_sample_size=n,
            )
        return self._inner.evaluate(records, current_phase, target_phase)


# ---------------------------------------------------------------------------
# CompositeGate — combine multiple gates
# ---------------------------------------------------------------------------


class CompositeGate:
    """Combine multiple gates with AND / OR semantics.

    ``CompositeGate.all_of(gates)`` advances only when **every**
    sub-gate advances (conjunction / strict). Useful when you want
    statistical evidence AND a minimum-volume floor AND operator
    approval.

    ``CompositeGate.any_of(gates)`` advances when **any** sub-gate
    advances (disjunction / permissive). Useful when different
    evidence types can each independently justify advancement.

    The returned decision's rationale concatenates sub-decisions so
    operators can see which sub-gate(s) drove the result.
    """

    def __init__(self, gates: list[Gate], *, mode: str) -> None:
        if mode not in ("all", "any"):
            raise ValueError(f"mode must be 'all' or 'any'; got {mode!r}")
        if not gates:
            raise ValueError("CompositeGate requires at least one sub-gate")
        self._gates = list(gates)
        self._mode = mode

    @classmethod
    def all_of(cls, gates: list[Gate]) -> CompositeGate:
        """Advance only when every sub-gate advances."""
        return cls(gates, mode="all")

    @classmethod
    def any_of(cls, gates: list[Gate]) -> CompositeGate:
        """Advance when any sub-gate advances."""
        return cls(gates, mode="any")

    @property
    def gates(self) -> list[Gate]:
        return list(self._gates)

    @property
    def mode(self) -> str:
        return self._mode

    def evaluate(
        self,
        records: list[ClassificationRecord],
        current_phase: Phase,
        target_phase: Phase,
        /,
    ) -> GateDecision:
        sub_decisions = [g.evaluate(records, current_phase, target_phase) for g in self._gates]
        if self._mode == "all":
            target_better = all(d.target_better for d in sub_decisions)
        else:  # any
            target_better = any(d.target_better for d in sub_decisions)
        rationale_parts = [
            f"[{i} {'✓' if d.target_better else '✗'}] {d.rationale}"
            for i, d in enumerate(sub_decisions)
        ]
        rationale = f"CompositeGate.{self._mode}_of: " + " | ".join(rationale_parts)
        # Merge the most informative stats — prefer the first sub-decision
        # with statistical content (all gates fire the same comparison).
        stats_from = next(
            (d for d in sub_decisions if d.p_value is not None or d.paired_sample_size > 0),
            sub_decisions[0],
        )
        return GateDecision(
            target_better=target_better,
            rationale=rationale,
            p_value=stats_from.p_value,
            paired_sample_size=stats_from.paired_sample_size,
            current_accuracy=stats_from.current_accuracy,
            target_accuracy=stats_from.target_accuracy,
        )


__all__ = [
    "DEFAULT_ALPHA",
    "DEFAULT_MIN_PAIRED",
    "AccuracyMarginGate",
    "CompositeGate",
    "Gate",
    "GateDecision",
    "ManualGate",
    "McNemarGate",
    "MinVolumeGate",
    "next_phase",
]
