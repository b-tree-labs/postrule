# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
#
# Confidence-gated escalation (#198, EPIC: LLM Use Efficiency MVP).
#
# The local ML head answers most requests for ~$0. When it is *not*
# confident, the switch escalates that one request to a stronger model.
# This module is the pure decision core: given a prediction's confidence
# and a threshold tau, decide accept-vs-escalate, and calibrate tau from a
# target accuracy floor on held-out data.
#
# Scope boundaries (deliberate):
#   * This module makes NO network calls and emits NO telemetry. The
#     escalation transport (cloud vs local-only model) and the Tier-1
#     efficiency telemetry are wired separately, the latter only AFTER the
#     #197 data-governance gate lands (build collects data -> governance
#     first). Here we only decide.
#   * The ML head's MLPrediction.confidence is already max-class
#     predict_proba (ml.py). For well-calibrated probabilities, train the
#     head's estimator under CalibratedClassifierCV upstream; this gate is
#     agnostic to how confidence was produced.

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum

from .ml import MLPrediction


class EscalationTarget(str, Enum):
    """Where a low-confidence request escalates TO.

    The target is itself a privacy control (#197): CLOUD sends the
    request's content to an external provider; LOCAL_ONLY escalates to a
    bigger model inside the customer's own boundary so nothing leaves —
    the option cloud-only routers architecturally cannot offer.
    """

    CLOUD = "cloud"
    LOCAL_ONLY = "local_only"


@dataclass(frozen=True)
class EscalationDecision:
    """The verdict for one request: keep the local answer, or escalate."""

    escalate: bool
    confidence: float
    threshold: float
    target: EscalationTarget
    reason: str


class ConfidenceGate:
    """Escalate when the local head's confidence falls below ``threshold``.

    ``threshold`` (tau) is the accept/escalate boundary in the same units
    as :attr:`MLPrediction.confidence` (max-class probability, 0..1).
    Derive it from a target accuracy floor with :func:`calibrate_threshold`
    rather than guessing.
    """

    def __init__(
        self,
        threshold: float,
        target: EscalationTarget = EscalationTarget.CLOUD,
    ) -> None:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"threshold must be in [0, 1], got {threshold}")
        self.threshold = float(threshold)
        self.target = target

    def decide(self, prediction: MLPrediction) -> EscalationDecision:
        """Decide accept-vs-escalate for a single local prediction."""
        conf = float(prediction.confidence)
        escalate = conf < self.threshold
        reason = (
            f"confidence {conf:.3f} < tau {self.threshold:.3f} -> escalate"
            if escalate
            else f"confidence {conf:.3f} >= tau {self.threshold:.3f} -> keep local"
        )
        return EscalationDecision(
            escalate=escalate,
            confidence=conf,
            threshold=self.threshold,
            target=self.target,
            reason=reason,
        )


def calibrate_threshold(
    confidences: Sequence[float],
    correct: Sequence[bool],
    accuracy_floor: float,
    *,
    min_kept: int = 1,
) -> float:
    """Pick the LOWEST tau whose KEPT (accepted) predictions meet the floor.

    On held-out data we observe, per example, the local head's
    ``confidence`` and whether it was ``correct``. We accept (keep local)
    everything with ``confidence >= tau`` and escalate the rest. We want
    the smallest tau — i.e. escalate as *little* as possible, maximizing
    the cheap local coverage — such that accuracy among the kept set is at
    least ``accuracy_floor``.

    Returns the calibrated tau in [0, 1]. If no tau achieves the floor
    (the head is too unreliable even at its most confident), returns a tau
    just above the maximum observed confidence so the gate escalates
    everything — the conservative, accuracy-preserving choice.

    Raises ``ValueError`` on length mismatch, empty input, or a floor
    outside [0, 1].
    """
    if len(confidences) != len(correct):
        raise ValueError("confidences and correct must be the same length")
    if not confidences:
        raise ValueError("need at least one calibration example")
    if not 0.0 <= accuracy_floor <= 1.0:
        raise ValueError(f"accuracy_floor must be in [0, 1], got {accuracy_floor}")

    pairs = sorted(zip(confidences, correct, strict=True), key=lambda p: p[0])
    # Candidate taus = each observed confidence (accept those >= candidate).
    # Walk from the lowest tau (keep most) upward; first one that satisfies
    # the floor with >= min_kept kept is the max-coverage solution.
    candidates = sorted({float(c) for c in confidences})
    for tau in candidates:
        kept = [ok for conf, ok in pairs if conf >= tau]
        if len(kept) < min_kept:
            continue
        acc = sum(kept) / len(kept)
        if acc >= accuracy_floor:
            return float(tau)

    # Floor unachievable at any threshold -> escalate everything.
    return float(max(candidates)) + 1e-9


@dataclass(frozen=True)
class EscalationOutcome:
    """The resolved prediction plus how we got it (for telemetry/audit)."""

    prediction: MLPrediction
    source: str  # "local" | "escalated:cloud" | "escalated:local_only" | "local_fallback"
    escalated: bool


DEFAULT_UPGRADE_URL = "https://app.postrule.ai/billing/upgrade"


def upsell_message(
    reason: str = "your plan's escalation capacity",
    url: str = DEFAULT_UPGRADE_URL,
) -> str:
    """A POLITE, one-click 'do more' upsell — never a 'pay up' nag.

    Surfaced to the operator (key owner) when escalation degrades to local.
    Tone: it's working, here's how to get more. Includes a one-click resolve
    link so an inattentive admin can fix it instantly.
    """
    return (
        f"Postrule: heads up — you've reached {reason}, so requests are running "
        f"locally for now (your code keeps working, just without cloud escalation). "
        f"Want the full-quality answers back? One click: {url}"
    )


# ---------------------------------------------------------------------------
# Operator education — conservative, proactive (NOT a nag)
# ---------------------------------------------------------------------------
#
# Distinct from the cap upsell above: that one is REACTIVE (you hit a wall).
# This is PROACTIVE and rare — an occasional "did you know" so an operator who
# has been quietly using Postrule learns what they have and could do. Same gate
# as the upsell: authenticated key owner ONLY (never keyless / packaged 3rd
# parties — see resolve_escalation + #205), suppressible, and at most one hint
# per long silence so it never reads as nagging.

DEFAULT_LEARN_URL = "https://postrule.ai/what-you-can-do"
HINT_MIN_SILENCE_DAYS = 30
HINT_MIN_ACCOUNT_AGE_DAYS = 14


def due_for_hint(
    days_since_last_hint: float,
    account_age_days: float,
    *,
    min_silence_days: float = HINT_MIN_SILENCE_DAYS,
    min_age_days: float = HINT_MIN_ACCOUNT_AGE_DAYS,
) -> bool:
    """Conservative cadence policy for an educational hint.

    True only when BOTH hold: the account is established (>= ``min_age_days``,
    so we don't lecture brand-new users) AND it's been a long silence since the
    last hint (>= ``min_silence_days``, so at most ~one hint per period). Never
    keyed off hitting a cap — that's the reactive upsell. Pure/time-free: the
    caller supplies the elapsed days, so it's trivially testable.
    """
    return account_age_days >= min_age_days and days_since_last_hint >= min_silence_days


def educational_hint(tip: str, url: str = DEFAULT_LEARN_URL) -> str:
    """A gentle, educational 'did you know' — what they have and could do.

    Not a nag and not tied to a limit: surfaced rarely (see :func:`due_for_hint`),
    to the authenticated owner only, and suppressible. Tone: teach, don't push.
    """
    return f"Postrule tip: {tip} Learn more: {url}"


def resolve_escalation(
    local: MLPrediction,
    decision: EscalationDecision,
    escalate_call: Callable[[], MLPrediction] | None = None,
    *,
    on_fallback: Callable[[str], None] | None = None,
) -> EscalationOutcome:
    """Apply an escalation decision such that the caller ALWAYS gets a result.

    This is the load-bearing guarantee behind hard-capping a band: exceeding
    the band (a cap-429) — or any cloud/network/timeout failure on the
    escalation call — degrades to the LOCAL prediction ("runs blind"). It
    never raises into the customer's pipeline. Their code always runs; at
    worst it runs without the escalated answer.

    - decision.escalate is False, or no ``escalate_call`` provided -> local.
    - escalate_call() succeeds -> its result, tagged by target.
    - escalate_call() raises ANYTHING (cap 429, network, provider error)
      -> local prediction, source="local_fallback".

    ``on_fallback`` is an OPTIONAL notifier invoked (with a polite
    :func:`upsell_message`) only when we fall back. **It is the caller's job to
    pass this ONLY for an authenticated key owner.** A keyless / library-
    embedded ("unauthenticated") deployment passes ``None`` -> silent: a
    second-degree consumer of a library that depends on Postrule-free is never
    nagged or billed; for them Postrule is just a local library. The nag is
    scoped to the operator who configured a key, and is an upsell, not a dun.
    """
    if not decision.escalate or escalate_call is None:
        return EscalationOutcome(local, "local", escalated=False)
    try:
        escalated = escalate_call()
    except Exception:  # noqa: BLE001 — deliberate: never break the caller's run
        if on_fallback is not None:
            try:
                on_fallback(upsell_message())
            except Exception:  # noqa: BLE001 — notification must never break the run either
                pass
        return EscalationOutcome(local, "local_fallback", escalated=False)
    return EscalationOutcome(escalated, f"escalated:{decision.target.value}", escalated=True)
