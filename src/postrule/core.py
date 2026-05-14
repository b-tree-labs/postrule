# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Core types and the LearnedSwitch class.

The six-phase lifecycle follows the paper outline (§3.1):

    RULE → MODEL_SHADOW → MODEL_PRIMARY → ML_SHADOW → ML_WITH_FALLBACK → ML_PRIMARY

In RULE the rule is the decision-maker. In MODEL_SHADOW the rule still
decides; a language model runs alongside, its prediction captured on every
outcome for later analysis. Phases 2+ add their own routing rules.
"""

from __future__ import annotations

import contextlib
import inspect
import threading
import time
import weakref
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from postrule.ml import MLHead
    from postrule.models import ModelClassifier
    from postrule.storage import Storage
    from postrule.telemetry import TelemetryEmitter

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Phase(str, Enum):
    """Lifecycle phase — see paper §3.1 Table 1."""

    RULE = "RULE"
    MODEL_SHADOW = "MODEL_SHADOW"
    MODEL_PRIMARY = "MODEL_PRIMARY"
    ML_SHADOW = "ML_SHADOW"
    ML_WITH_FALLBACK = "ML_WITH_FALLBACK"
    ML_PRIMARY = "ML_PRIMARY"


class Verdict(str, Enum):
    """Label for an observed classification decision."""

    CORRECT = "correct"
    INCORRECT = "incorrect"
    UNKNOWN = "unknown"


# Module-level frozenset of permitted outcome strings. Built once at
# import time so ``record_verdict``'s validation doesn't rebuild a set
# per call (v1-readiness §2 finding #23).
_VERDICT_VALUES: frozenset[str] = frozenset(o.value for o in Verdict)


def _per_classifier_correct(
    classifier_output: Any,
    chosen_label: Any,
    outcome: str,
) -> bool | None:
    """Decide whether a layer (rule / model / ml) "was correct" for
    a given verdict-bearing classification.

    Honest with what we know: we have the chosen label, each layer's
    prediction (or None when the layer wasn't active), and a verdict
    that applies to the *chosen* label only.

    Decision table:

    - layer wasn't active (output is None)              → None
    - outcome == "unknown" (no judgement made)          → None
    - layer's prediction matches the chosen label       → True/False
      mirroring the chosen label's correctness
    - layer's prediction differs from the chosen label  → None
      (we don't know whether the OTHER label was the right one)

    This is intentionally lossy on the "differs" case rather than
    guessing — null is honest, true/false would smuggle in an
    unjustified assumption. Paired-correctness analytics on the
    server side know to treat null as "not paired".
    """
    if classifier_output is None:
        return None
    if outcome == Verdict.UNKNOWN.value:
        return None
    if classifier_output != chosen_label:
        return None
    return outcome == Verdict.CORRECT.value


# ---------------------------------------------------------------------------
# Labels + action dispatch
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Label:
    """A classification output, optionally paired with an action.

    A :class:`Label` is a **label-based conditional expression**:
    ``name`` is the label (the classifier's possible output); ``on``
    is the consequent — the callable invoked when the switch's
    chosen output equals ``name``. Conceptually::

        if classify(input) == label.name:
            label.on(input)

    The "label" framing is the standard ML term for a classifier's
    output category. The "conditional-expression" framing is
    Postrule's: every label carries an optional *consequent* that
    Postrule evaluates on a match, so a single ``classify()`` call
    produces both a decision and (when configured) the action that
    the decision implies.

    Failures inside ``on`` are captured, not propagated; the caller
    sees ``action_raised`` populated on the result and record.
    """

    name: str
    on: Callable[[Any], Any] | None = None


# Forms accepted for the decorator/switch ``labels=`` argument.
LabelLike = str | Label
LabelsArg = list[LabelLike] | dict[str, Callable[[Any], Any]]


def _normalize_labels(labels: LabelsArg | None) -> list[Label]:
    """Coerce list[str] | list[Label] | dict[str, Callable] → list[Label]."""
    if labels is None:
        return []
    if isinstance(labels, dict):
        return [Label(name=k, on=v) for k, v in labels.items()]
    out: list[Label] = []
    for item in labels:
        if isinstance(item, Label):
            out.append(item)
        elif isinstance(item, str):
            out.append(Label(name=item))
        else:
            raise TypeError(f"labels entries must be str or Label; got {type(item).__name__}")
    return out


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass
class ClassificationResult:
    """The return value of ``classify`` / ``dispatch``.

    ``label`` is the switch's chosen classification output. Named
    ``label`` (not ``output``) to match the rest of the API's
    vocabulary — the same word appears in ``labels=``, :class:`Label`,
    and the "label-based conditional expression" framing.

    Includes fluent verdict shortcuts (``mark_correct``,
    ``mark_incorrect``, ``mark_unknown``) that append a verdict
    record to the originating switch's storage. When the result
    was produced by a detached construction (tests, manual instantiation),
    the shortcuts raise :class:`RuntimeError` instead of silently
    no-op'ing.

    **Shadow observations travel on the result**, not on the
    switch. Each :class:`ClassificationResult` carries the
    per-source predictions captured during its own classify call —
    so calling ``.mark_correct()`` minutes later (or from a
    different thread, or after many intervening calls) still
    attaches the CORRECT shadow data to the verdict record. This
    is the fix for the shadow-stash cross-contamination race
    (v1-readiness.md §2 finding #3).
    """

    label: Any
    source: str  # "rule" | "model" | "ml" | "rule_fallback"
    confidence: float
    phase: Phase
    # Populated when the chosen label has an ``on=`` callable.
    action_result: Any = None
    action_raised: str | None = None
    action_elapsed_ms: float | None = None
    # Private back-references so ``.mark_*()`` methods can record a
    # verdict without the caller re-threading the switch and input.
    # Per-call shadow observations travel on the result so
    # concurrent classifies can't cross-contaminate each other's
    # verdict records. None of these private fields participate in
    # equality or repr; none are serialized.
    _switch: Any = field(default=None, repr=False, compare=False)
    _input: Any = field(default=None, repr=False, compare=False)
    _rule_output: Any = field(default=None, repr=False, compare=False)
    _model_output: Any = field(default=None, repr=False, compare=False)
    _model_confidence: float | None = field(default=None, repr=False, compare=False)
    _ml_output: Any = field(default=None, repr=False, compare=False)
    _ml_confidence: float | None = field(default=None, repr=False, compare=False)
    # Sentinel: the live exception object captured by ``_maybe_dispatch``
    # when an ``on=`` handler raised. ``dispatch`` consults this AFTER
    # auto_log + telemetry to decide whether to re-raise (governed by
    # ``SwitchConfig.propagate_action_exceptions``). Never serialized,
    # never compared.
    _action_exception: BaseException | None = field(default=None, repr=False, compare=False)

    def mark_correct(self) -> None:
        """Record that this classification matched ground truth."""
        self._mark(Verdict.CORRECT)

    def mark_incorrect(self) -> None:
        """Record that this classification did not match ground truth."""
        self._mark(Verdict.INCORRECT)

    def mark_unknown(self) -> None:
        """Record that ground truth is unknown / still pending."""
        self._mark(Verdict.UNKNOWN)

    def _mark(self, verdict: Verdict) -> None:
        if self._switch is None:
            raise RuntimeError(
                "ClassificationResult.mark_*() requires the result to "
                "come from a live switch's classify() or dispatch(); "
                "this result has no switch back-reference."
            )
        self._switch.record_verdict(
            input=self._input,
            label=self.label,
            outcome=verdict.value,
            source=self.source,
            confidence=self.confidence,
            _result_ctx=self,
        )


@dataclass(frozen=True)
class ClassificationRecord:
    """One ``(input, label, outcome)`` row in the outcome log.

    ``label`` is the decision that was actually returned by the
    switch at classify time. Per-source raw predictions live in
    ``rule_output`` / ``model_output`` / ``ml_output`` (the word
    "output" is reserved for those raw per-source predictions;
    the final decision is the ``label``).
    """

    timestamp: float
    input: Any
    label: Any
    outcome: str  # Verdict value
    source: str  # which path produced `label` at classify time
    confidence: float
    # Phase 1+ shadow observations (optional; omitted at Phase 0).
    rule_output: Any | None = None
    model_output: Any | None = None
    model_confidence: float | None = None
    # Phase 3+ ML shadow observations.
    ml_output: Any | None = None
    ml_confidence: float | None = None
    # Action-dispatch observations (populated when the chosen label has
    # ``on=`` set and dispatch() fired it).
    action_result: Any | None = None
    action_raised: str | None = None
    action_elapsed_ms: float | None = None


@dataclass
class SwitchStatus:
    """Observable state of a switch at a point in time."""

    name: str
    phase: Phase
    outcomes_total: int
    outcomes_correct: int
    outcomes_incorrect: int
    model_version: str | None = None
    # Phase 1 observability — fraction of outcomes where the language model agreed
    # with the rule. ``None`` when no shadow observations are recorded.
    shadow_agreement_rate: float | None = None
    # Phase 3 observability — fraction where ML shadow matched the primary.
    ml_agreement_rate: float | None = None
    # Phase 5 circuit-breaker state.
    circuit_breaker_tripped: bool = False


@dataclass(frozen=True)
class BulkVerdict:
    """One entry in a :meth:`LearnedSwitch.bulk_record_verdicts` batch.

    ``input``, ``label``, ``outcome`` are required; ``source`` and
    ``confidence`` default to the same values ``record_verdict``
    uses when called without them. ``BulkVerdict`` is a plain
    dataclass (no business logic) so callers can construct one per
    row from any source — CSV, JSON, DB query, reviewer-tool
    export.
    """

    input: Any
    label: Any
    outcome: str  # a Verdict value ("correct" / "incorrect" / "unknown")
    source: str = "bulk"
    confidence: float = 1.0


@dataclass
class BulkVerdictSummary:
    """What came back from a bulk_record_verdicts call.

    - ``total`` — rows attempted.
    - ``recorded`` — rows that landed in storage.
    - ``failed`` — rows the storage refused (exceptions absorbed;
      a single flaky record mustn't poison the whole batch).
    - ``auto_advance_decision`` — populated with the gate's
      decision when auto-advance fired at end-of-batch; ``None``
      otherwise.
    """

    total: int = 0
    recorded: int = 0
    failed: int = 0
    auto_advance_decision: Any = None


# Phase ordering for <= / >= comparisons. RULE = 0, ML_PRIMARY = 5.
_PHASE_ORDER: dict[Phase, int] = {
    Phase.RULE: 0,
    Phase.MODEL_SHADOW: 1,
    Phase.MODEL_PRIMARY: 2,
    Phase.ML_SHADOW: 3,
    Phase.ML_WITH_FALLBACK: 4,
    Phase.ML_PRIMARY: 5,
}


@dataclass
class SwitchConfig:
    """Runtime configuration for a switch.

    Two phase-related axes are tracked separately:

    - ``starting_phase`` — the phase the switch begins in. Default
      :data:`Phase.RULE` (safety-first). Set to a higher phase for
      LLM-as-teacher bootstrap (``MODEL_PRIMARY``), porting an
      existing language model classifier, or hybrid steady-state designs.
    - ``phase_limit`` — the ceiling. ``advance()`` refuses to cross
      it. Default :data:`Phase.ML_PRIMARY` (no cap — full autonomy
      permitted when evidence earns it). Set lower to constrain
      how far the switch is allowed to graduate.

    ``safety_critical=True`` is a convenience flag that implies
    ``phase_limit = ML_WITH_FALLBACK`` and refuses construction in
    ``ML_PRIMARY``. It's kept for backward-compat and readability;
    new code can use ``phase_limit=Phase.ML_WITH_FALLBACK`` directly
    for the same effect (or any other ceiling).

    The legacy ``phase=...`` keyword is accepted as an alias for
    ``starting_phase=...`` and emits a ``DeprecationWarning``. It
    will be removed in a future major release.
    """

    confidence_threshold: float = 0.85
    starting_phase: Phase = Phase.RULE
    phase_limit: Phase = Phase.ML_PRIMARY
    safety_critical: bool = False
    # Gate used by ``LearnedSwitch.advance()`` to decide whether the
    # switch has earned its next phase. Defaults to None → resolved
    # to the default McNemarGate at switch construction time.
    gate: Any = None
    # Automatic verdict-free logging: every ``classify``/``dispatch``
    # call auto-appends a ClassificationRecord with
    # ``outcome=Verdict.UNKNOWN`` and all captured shadow observations.
    # Later ``record_verdict`` calls append verdict-bearing rows that
    # the gate prefers for paired-correctness math. Set
    # ``auto_record=False`` to suppress the UNKNOWN rows.
    auto_record: bool = True
    # Automatic graduation: every ``auto_advance_interval`` calls to
    # ``record_verdict``, the switch asks the gate whether it's
    # earned the next phase. Set ``auto_advance=False`` for
    # operator-only workflows.
    #
    # Default interval is 500 — each gate evaluation walks the whole
    # outcome log, so a lower interval bills the walk more often and
    # shows up as a p99 spike at the boundary (v1-readiness.md §2
    # finding #30). 500 keeps the gate cost < 0.2% of classify
    # latency on a 10k-record log.
    auto_advance: bool = True
    auto_advance_interval: int = 500
    # Drift gate: the Gate the auto-demote loop calls to detect when the
    # current phase's decision-maker has been overtaken by the rule. The
    # gate is direction-agnostic; the auto-demote path passes the rule
    # as the comparison target. ``None`` resolves to ``McNemarGate()`` at
    # switch construction so the demotion check is symmetric with the
    # advance check by default. Pass any ``Gate`` for a different
    # statistical test.
    drift_gate: Any = None
    # Automatic demotion: every ``auto_advance_interval`` records, the
    # switch also asks ``drift_gate`` whether the rule has reclaimed the
    # lead and demotes one phase if so. Set ``auto_demote=False`` for
    # operator-only demotion via :meth:`LearnedSwitch.demote`.
    auto_demote: bool = True
    # Cooldown: after ANY phase change (advance or demote), suppress
    # both auto-checks until ``phase_cooldown_records`` more verdicts
    # accumulate. Prevents thrash on data near the gate's decision
    # boundary. Defaults to the typical ``min_paired`` so a fresh
    # paired-correctness window is collected before re-evaluating.
    phase_cooldown_records: int = 200
    # Optional hook fired after every successful ``record_verdict``.
    # Receives the persisted :class:`ClassificationRecord`. Useful
    # for mirroring verdicts to an external audit store, triggering
    # metrics, sending webhooks.
    on_verdict: Callable[[Any], None] | None = None
    # Optional verdict source that runs automatically on every
    # ``classify`` / ``dispatch`` call. When set, the switch
    # classifies, then routes the (input, label) pair through the
    # verifier's ``judge`` to obtain a verdict, then writes a
    # verdict-bearing record (replacing the auto-record UNKNOWN
    # row). Pair with :func:`postrule.default_verifier` for the
    # auto-detection factory.
    verifier: Any = None
    # Fraction of classifications routed through the verifier.
    # ``1.0`` (default) verifies every call. Use a lower value
    # (e.g., ``0.1`` for 10%) when the verifier is expensive
    # (cloud language model, large committee) and full coverage isn't
    # required for the gate's statistical power. Sampling is
    # uniform random per-call.
    verifier_sample_rate: float = 1.0
    # When ``True``, ``dispatch`` / ``adispatch`` re-raise the original
    # exception thrown by an ``on=`` handler AFTER ``action_raised`` and
    # ``action_elapsed_ms`` have been recorded on the result and the
    # auto-record / verifier / telemetry path has run to completion.
    # The storage row with ``action_raised`` populated lands first, so
    # the failure is observable in the outcome log even when the caller
    # also catches it.
    #
    # Default ``False`` preserves the production-safe contract: a
    # misbehaving handler never loses the classification decision (the
    # exception is captured to ``action_raised`` and stringified, and
    # ``dispatch`` returns normally). Postmortem happens via telemetry.
    #
    # Set ``True`` to restore pre-Postrule exception parity for callers
    # that wrap their classify+act sites in ``try/except`` and expect
    # handler exceptions to bubble. ``KeyboardInterrupt`` and
    # ``SystemExit`` propagate regardless of this knob.
    propagate_action_exceptions: bool = False
    # Deprecated alias for starting_phase. None means "not supplied"
    # and the dataclass falls back to starting_phase's default.
    phase: Phase | None = None

    def __post_init__(self) -> None:
        if self.phase is not None:
            import warnings

            warnings.warn(
                "SwitchConfig(phase=...) is deprecated; use "
                "starting_phase=... instead. The phase parameter will "
                "be removed in a future major release.",
                DeprecationWarning,
                stacklevel=3,
            )
            # Alias wins over the default starting_phase; explicit
            # starting_phase (non-default) takes precedence if both set.
            if self.starting_phase is Phase.RULE:
                self.starting_phase = self.phase

        # safety_critical refuses ML_PRIMARY as either starting_phase
        # or as a permitted ceiling — gives the paper-§7.1 guarantee
        # its own explicit error message rather than the generic
        # starting_phase/phase_limit mismatch.
        if self.safety_critical and self.starting_phase is Phase.ML_PRIMARY:
            raise ValueError(
                "safety_critical switches cannot start in ML_PRIMARY; "
                "cap at ML_WITH_FALLBACK (paper §7.1)."
            )

        # Validate verifier_sample_rate.
        if not 0.0 <= self.verifier_sample_rate <= 1.0:
            raise ValueError(
                f"verifier_sample_rate must be in [0, 1]; got {self.verifier_sample_rate}"
            )

        # safety_critical caps the ceiling at ML_WITH_FALLBACK.
        if (
            self.safety_critical
            and _PHASE_ORDER[self.phase_limit] > _PHASE_ORDER[Phase.ML_WITH_FALLBACK]
        ):
            self.phase_limit = Phase.ML_WITH_FALLBACK

        # starting_phase cannot exceed phase_limit.
        if _PHASE_ORDER[self.starting_phase] > _PHASE_ORDER[self.phase_limit]:
            raise ValueError(
                f"starting_phase={self.starting_phase.name} exceeds "
                f"phase_limit={self.phase_limit.name}. The switch cannot "
                f"start above its own ceiling."
            )


# ---------------------------------------------------------------------------
# LearnedSwitch
# ---------------------------------------------------------------------------


RuleFunc = Callable[[Any], Any]


def _input_hash(value: Any) -> str:
    """Stable short identifier for a classifier input.

    Used by :meth:`LearnedSwitch.export_for_review` and
    :meth:`LearnedSwitch.apply_reviews` to correlate reviewer
    annotations back to originating records. Uses the stdlib
    ``hashlib.sha256`` on the JSON-serialized form of the input,
    falling back to ``repr`` for non-JSON-serializable values.
    The hash is **truncated to 16 hex chars** — enough collision
    resistance for a per-switch review queue; not a cryptographic
    identifier. Callers writing cross-process reviewer tooling
    should use the full input payload, not the hash, as the join
    key when collisions matter.
    """
    import hashlib
    import json

    try:
        encoded = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    except (TypeError, ValueError):
        encoded = repr(value).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _is_real_label(label: Any) -> bool:
    """Return True if ``label`` looks like a usable decision.

    A misbehaving model adapter or ML head can return ``label=""`` (or
    a whitespace-only string, or None). Those are not decisions. The
    switch must treat them as no-decision and fall back to the rule —
    silently emitting an empty string downstream is the bug shape we
    saw in issue #138.

    Non-string labels (typed enums, ints) are passed through as-is;
    only the string-empty / None case trips the fallback. ``str.strip``
    is cheap and defensive: ``label=" "`` is also empty.
    """
    if label is None:
        return False
    if isinstance(label, str):
        return label.strip() != ""
    return True


def _clamp_conf(v: float | None) -> float | None:
    """Clamp a confidence value to ``[0, 1]``; return None for None / NaN.

    Defensive: adapters can legitimately return any float (0.99999,
    1.0001, -0.0). Keeping every downstream gate-math and
    confidence-threshold comparison tolerant of ill-typed values is
    more churn than clamping at the one entry point. v1-readiness.md
    §2 finding #24.
    """
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return max(0.0, min(1.0, f))


class _VerdictHolder:
    """Yielded by :meth:`LearnedSwitch.verdict_for` — stores the in-flight
    classification and exposes methods that record the verdict.
    """

    def __init__(self, *, switch: Any, input: Any, result: ClassificationResult) -> None:
        self._switch = switch
        self._input = input
        self.result = result
        self._recorded = False

    def correct(self) -> None:
        """Record that the classification matched ground truth."""
        self._mark(Verdict.CORRECT)

    def incorrect(self) -> None:
        """Record that the classification did not match ground truth."""
        self._mark(Verdict.INCORRECT)

    def unknown(self) -> None:
        """Record that ground truth is unknown / pending."""
        self._mark(Verdict.UNKNOWN)

    def _mark(self, verdict: Verdict) -> None:
        if self._recorded:
            return
        self._recorded = True
        self._switch.record_verdict(
            input=self._input,
            label=self.result.label,
            outcome=verdict.value,
            source=self.result.source,
            confidence=self.result.confidence,
        )


def _derive_author(switch_name: str, rule: RuleFunc) -> str:
    """Derive a stable author ID from the caller's code location.

    Strategy: prefer the rule function's module (that's where the
    switch semantically lives), falling back to a walk up the call
    stack until we leave the ``postrule`` package. The returned
    string has the shape ``"@<module>:<switch_name>"`` — stable
    per-deployment, unique per-switch-per-module, and unspoofable
    without editing the source that defines the switch.

    Users who want a different provenance scheme pass ``author=...``
    explicitly; the explicit value always wins.
    """
    module = getattr(rule, "__module__", None)
    if module and module != "__main__" and not module.startswith("postrule."):
        return f"@{module}:{switch_name}"

    # Rule has no useful __module__ (lambda, __main__, or postrule-internal).
    # Walk the stack to find the first caller outside of postrule.
    frame = inspect.currentframe()
    try:
        while frame is not None:
            caller_module = frame.f_globals.get("__name__", "")
            if caller_module and not caller_module.startswith("postrule."):
                return f"@{caller_module}:{switch_name}"
            frame = frame.f_back
    finally:
        del frame  # Avoid cyclic references.

    # Last-resort fallback — should be unreachable in practice.
    return f"@unknown:{switch_name}"


# Process-level registry of active switches keyed by (id(storage), name).
# Used to detect collisions when two LearnedSwitches would share a storage
# backend AND a name — a silent shared outcome log is almost always a bug.
# Entries auto-expire when a switch is garbage-collected.
_SWITCH_REGISTRY: weakref.WeakValueDictionary[tuple[int, str], LearnedSwitch] = (
    weakref.WeakValueDictionary()
)
_SWITCH_REGISTRY_LOCK = threading.Lock()


def _derive_name_from_rule(rule: RuleFunc) -> str:
    """Derive a switch name from the rule function's ``__name__``.

    Raises :class:`ValueError` for lambdas and other rules with no
    stable name — those cannot be auto-named safely because the
    storage key must survive process restarts.
    """
    rule_name = getattr(rule, "__name__", None)
    if not rule_name or rule_name == "<lambda>":
        raise ValueError(
            "name is required when rule has no stable __name__ "
            "(e.g., lambda, partial, or wrapped callable). Pass "
            "name=... explicitly."
        )
    return rule_name


class LearnedSwitch:
    """Graduated-autonomy classification primitive.

    Args:
        rule: Pure function ``input → output`` that produces the
            safety-floor decision. Never modified by the library.
        name: Stable identifier used in logs, audit records, and
            storage keys. Optional — when omitted, auto-derived from
            ``rule.__name__``. Rules with no stable name (lambdas,
            wrapped callables) must pass ``name`` explicitly. If two
            switches would share the same storage backend AND the
            same name, construction raises :class:`ValueError` —
            silent shared outcome logs are almost always a bug.
        author: Principal associated with the switch (opaque
            provenance string; logged, surfaced in audit output,
            used by the regulated-tier audit chain). Optional —
            when omitted, auto-derived from the rule's module plus
            the switch name as ``"@<module>:<name>"``. Pass an
            explicit value to use a custom provenance scheme (team
            handle, service account, compliance ID, etc.); the
            explicit value always wins. Cannot be an empty string.
        labels: The switch's label-based conditional expressions —
            each label names a possible classifier output; pairing a
            label with an ``on=`` action turns the label into a
            dispatch clause. Accepted forms: ``list[str]``,
            ``list[Label]``, or ``dict[str, Callable]``.
        starting_phase: Lifecycle phase the switch begins in.
            Default :data:`Phase.RULE`. Convenience shortcut for
            ``config=SwitchConfig(starting_phase=...)``.
        phase_limit: Upper bound on phase advancement. Default
            :data:`Phase.ML_PRIMARY` (no cap). Convenience shortcut
            for ``config=SwitchConfig(phase_limit=...)``.
        safety_critical: Shorthand for a switch that must never drop
            its rule floor — caps ``phase_limit`` at
            ``ML_WITH_FALLBACK`` and refuses construction in
            ``ML_PRIMARY``. See example 03.
        confidence_threshold: Minimum model/ML confidence for the
            switch to adopt their decision over the rule floor.
            Default 0.85.
        config: Optional :class:`SwitchConfig`. Power-user escape
            hatch when you need full control. **Mutually exclusive**
            with ``starting_phase`` / ``phase_limit`` /
            ``safety_critical`` / ``confidence_threshold`` — pass
            one shape or the other, not both.
        storage: Optional :class:`Storage` backend. When omitted, a
            :class:`BoundedInMemoryStorage` is installed so a bare
            switch cannot leak memory from unbounded outcome logging.
            Pass ``persist=True`` to derive a :class:`FileStorage`
            under ``./runtime/postrule/<name>/`` instead (wrapped in a
            :class:`ResilientStorage` so a transient disk failure
            does not take down classification).
        persist: When ``True`` and ``storage`` is not explicitly
            supplied, use a :class:`FileStorage` rooted at
            ``./runtime/postrule/`` wrapped in :class:`ResilientStorage`
            — transient I/O failures spill to an in-memory buffer
            that drains back when the primary recovers. Mutually
            exclusive with an explicit ``storage=`` argument. For
            hard-fail-on-IO-error semantics, pass
            ``storage=FileStorage(...)`` directly.
        model: Optional :class:`ModelClassifier` used in MODEL_SHADOW and
            MODEL_PRIMARY phases.
    """

    # ``__slots__`` declares every instance attribute. Attempts to
    # monkey-patch new attributes (``switch.rule = ...``, a different
    # storage, a custom flag) raise AttributeError at the set site
    # instead of silently creating a dead attribute. v1-readiness §2
    # finding #9: the rule in particular is security-load-bearing
    # (audit chain, safety-critical enforcement) — hot-swapping it
    # at runtime must not be a silent accident. Subclasses that need
    # their own attributes must declare their own ``__slots__``.
    __slots__ = (
        "name",
        "_rule",
        "author",
        "config",
        "_storage",
        "_model",
        "_ml_head",
        "_head_strategy",
        "_telemetry",
        "_lock",
        "_head_lock",
        "_head_loaded",
        "_circuit_tripped",
        "_records_since_advance_check",
        "_records_since_phase_change",
        "_labels_raw",
        "_label_index",
        "_persist",
        "__weakref__",
    )

    def __init__(
        self,
        *,
        rule: RuleFunc,
        name: str | None = None,
        author: str | None = None,
        labels: LabelsArg | None = None,
        # Hoisted SwitchConfig fields — the common case. These build a
        # SwitchConfig internally. Passing ``config=`` directly is the
        # power-user escape hatch; mixing the two forms raises.
        starting_phase: Phase | None = None,
        phase_limit: Phase | None = None,
        safety_critical: bool | None = None,
        confidence_threshold: float | None = None,
        gate: Any | None = None,
        auto_record: bool | None = None,
        auto_advance: bool | None = None,
        auto_advance_interval: int | None = None,
        on_verdict: Callable[[Any], None] | None = None,
        verifier: Any = None,
        verifier_sample_rate: float | None = None,
        config: SwitchConfig | None = None,
        storage: Storage | None = None,
        persist: bool = False,
        model: ModelClassifier | None = None,
        ml_head: MLHead | None = None,
        head_strategy: Any | None = None,
        telemetry: TelemetryEmitter | None = None,
    ) -> None:
        if rule is None or not callable(rule):
            raise ValueError("rule must be a callable")
        if ml_head is not None and head_strategy is not None:
            raise ValueError(
                "ml_head=... and head_strategy=... are mutually exclusive: "
                "pass either an explicit head or a strategy that picks one."
            )

        # ---- Name: autogen from rule.__name__ when absent -----------------
        name_was_autoderived = name is None
        if name is None:
            name = _derive_name_from_rule(rule)
        elif not name:
            raise ValueError("name cannot be empty; omit the argument for autogen")

        # ---- Author: autogen provenance ID when absent --------------------
        if author is None:
            author = _derive_author(name, rule)
        elif not author:
            raise ValueError(
                "author cannot be empty; omit the argument for autogen, "
                "or pass a non-empty provenance string."
            )

        # ---- Config: either the hoisted kwargs or an explicit config ------
        hoisted_config_kwargs = {
            "starting_phase": starting_phase,
            "phase_limit": phase_limit,
            "safety_critical": safety_critical,
            "confidence_threshold": confidence_threshold,
            "gate": gate,
            "auto_record": auto_record,
            "auto_advance": auto_advance,
            "auto_advance_interval": auto_advance_interval,
            "on_verdict": on_verdict,
            "verifier": verifier,
            "verifier_sample_rate": verifier_sample_rate,
        }
        supplied_hoisted = {k: v for k, v in hoisted_config_kwargs.items() if v is not None}
        if config is not None and supplied_hoisted:
            raise ValueError(
                "config=... is mutually exclusive with the hoisted shortcuts "
                f"({', '.join(sorted(supplied_hoisted))}). Pass one shape or "
                "the other, not both."
            )
        if config is None:
            # Build a SwitchConfig from the hoisted kwargs (or defaults).
            config = SwitchConfig(**supplied_hoisted) if supplied_hoisted else SwitchConfig()
        resolved_config = config

        # Resolve the gate lazily — keeps core's import graph free of
        # the viz/math dependencies that McNemarGate needs.
        if resolved_config.gate is None:
            from postrule.gates import McNemarGate

            resolved_config.gate = McNemarGate()

        # Same for the drift_gate. Default ``McNemarGate()`` makes the
        # demotion check symmetric with the advance check by default.
        if resolved_config.drift_gate is None:
            from postrule.gates import McNemarGate

            resolved_config.drift_gate = McNemarGate()

        # safety_critical refuses ML_PRIMARY even as a ceiling; this
        # is stricter than SwitchConfig's post_init (which only caps
        # the ceiling). Keeps the paper §7.1 architectural guarantee.
        if resolved_config.safety_critical and resolved_config.starting_phase is Phase.ML_PRIMARY:
            raise ValueError(
                "safety_critical switches cannot start in ML_PRIMARY; "
                "cap at ML_WITH_FALLBACK (paper §7.1)."
            )

        self.name = name
        self._rule = rule
        self.author = author
        self.config = resolved_config
        if storage is not None and persist:
            raise ValueError(
                "persist=True is incompatible with an explicit storage= "
                "argument. Pass one or the other."
            )
        if storage is None:
            if persist:
                # persist=True wraps a batched FileStorage in
                # ResilientStorage. Batching decouples classify
                # latency from disk-durability latency: append goes
                # to an in-memory queue, a background thread flushes
                # every 50 ms or every 64 records. See
                # docs/storage-backends.md for the full durability
                # contract. Users who want per-call fsync-strict
                # durability pass an explicit ``storage=FileStorage(
                # base_path, batching=False, fsync=True)`` and skip
                # the shortcut.
                from postrule.storage import FileStorage, ResilientStorage

                storage = ResilientStorage(FileStorage("runtime/postrule", batching=True))
            else:
                from postrule.storage import BoundedInMemoryStorage

                storage = BoundedInMemoryStorage()
        self._storage = storage

        # ---- Collision detection ------------------------------------------
        # If a LIVE switch already holds this (storage, name) pair, the new
        # one would silently share an outcome log. Fail loud. Auto-named
        # switches get a more informative error since the user didn't
        # pick the name and may not realize it collided.
        registry_key = (id(self._storage), name)
        with _SWITCH_REGISTRY_LOCK:
            existing = _SWITCH_REGISTRY.get(registry_key)
            if existing is not None and existing is not self:
                hint = f"A LearnedSwitch named {name!r} is already using this storage backend."
                if name_was_autoderived:
                    hint += (
                        " The name was auto-derived from rule.__name__; "
                        "pass an explicit name=... to the second switch "
                        "to disambiguate."
                    )
                else:
                    hint += " Use a different name= or a different storage."
                raise ValueError(hint)
            _SWITCH_REGISTRY[registry_key] = self

        self._model = model
        self._ml_head = ml_head
        self._head_strategy = head_strategy
        if telemetry is None:
            # No explicit emitter — consult the process-wide registry.
            # Integrations like the hosted cloud verdict pipe install a
            # factory at import time; absent any registration this falls
            # through to NullEmitter (zero overhead).
            from postrule.telemetry import get_default_emitter

            telemetry = get_default_emitter()
        self._telemetry = telemetry
        # Single RLock serializing all mutations to per-switch state
        # (breaker, auto-advance counter, config.starting_phase,
        # rotation-style bookkeeping). Classify calls take it while
        # checking the breaker and updating its state; record_verdict
        # takes it around the auto-advance counter; advance() takes it
        # across the read-log / evaluate-gate / mutate-phase sequence.
        # RLock (not Lock) because advance() invoked from within a
        # locked record_verdict re-enters.
        self._lock = threading.RLock()
        self._circuit_tripped: bool = False
        # Counter driving auto-advance — incremented by record_verdict
        # under the lock, reset when the gate is asked (whether it
        # said yes or no).
        self._records_since_advance_check: int = 0
        # Counter driving the phase-change cooldown. Reset to 0 on any
        # successful advance() or demote(); the auto-check loop
        # suppresses both gates while this is below
        # ``config.phase_cooldown_records``. Prevents thrash on data
        # near a gate's decision boundary. Initialized to a large value
        # so a freshly-constructed switch (no prior phase change to
        # cool down from) does not have the very first auto-check
        # blocked by the cooldown.
        self._records_since_phase_change: int = 10**9
        # Canonical labels list (Label objects). Assignment via the setter
        # normalizes strings / dicts and rebuilds the name index.
        self._labels_raw: list[Label] = _normalize_labels(labels)
        self._label_index: dict[str, Label] = {lbl.name: lbl for lbl in self._labels_raw}
        # Rehydrate persisted breaker state (paper §7.1 promise must
        # survive a process restart when durable storage is configured).
        self._persist: bool = bool(persist)
        # Head-mutation lock: serializes fit / state_bytes / load_state
        # so concurrent callers can't tear the trained pipeline. Distinct
        # from ``self._lock`` (the phase-mutation lock); classify()'s
        # predict() path is intentionally lock-free against this lock so
        # head persistence does NOT block hot-path classifications.
        self._head_lock = threading.Lock()
        # Set when the persisted head (if any) has finished loading.
        # Always set on switches without ``persist=True`` or without a
        # ``load_state``-capable head (nothing to wait for).
        self._head_loaded = threading.Event()
        if self._persist:
            self._load_breaker_state()
            # Restore the trained ML head on a background thread so
            # constructing a switch never blocks on a multi-second
            # pickle.loads (transformer-class heads can be hundreds of
            # MB). Callers that need trained state before serving the
            # first request use ``wait_until_head_loaded(timeout)``.
            head = ml_head
            if head is not None and hasattr(head, "load_state"):
                threading.Thread(
                    target=self._lazy_load_head_in_background,
                    name=f"postrule-head-load-{self.name}",
                    daemon=True,
                ).start()
            else:
                self._head_loaded.set()
        else:
            self._head_loaded.set()

        # Refuse HumanReviewerSource on the inline classify hot path.
        # HumanReviewerSource.judge() blocks up to ``timeout`` seconds
        # (default 30s) waiting for a human, which would stall every
        # sample-rated classify() call. The class is meant for cold-start
        # bulk ingestion or periodic drain workflows, not the inline
        # verifier= slot. Refuse explicitly with a pointer to the safe
        # patterns so users don't discover this in production.
        if (
            resolved_config.verifier is not None
            and type(resolved_config.verifier).__name__ == "HumanReviewerSource"
        ):
            raise ValueError(
                "refusing to construct LearnedSwitch with "
                "verifier=HumanReviewerSource(...): the inline classify "
                "hot path would block up to "
                f"{getattr(resolved_config.verifier, '_timeout', 30.0)}s "
                "per call waiting for a human reviewer. Use one of:\n"
                "  • bulk_record_verdicts_from_source(inputs, "
                "HumanReviewerSource(...)) for cold-start labeling, or\n"
                "  • export_for_review() / apply_reviews() for periodic "
                "drain workflows.\n"
                "See docs/verdict-sources.md for the supported patterns."
            )

        # Self-judgment guardrail when both ``model=`` and a
        # ``verifier=`` model judge are configured against the same
        # underlying language model. Same rationale as
        # :class:`JudgeSource.__init__` (G-Eval / MT-Bench /
        # Arena literature). Skipped when either side is absent or
        # the verifier doesn't expose its judge model.
        if self._model is not None and resolved_config.verifier is not None:
            judge_model = getattr(resolved_config.verifier, "_judge", None)
            if judge_model is not None:
                from postrule.verdicts import _same_model

                if _same_model(self._model, judge_model):
                    raise ValueError(
                        "refusing to construct LearnedSwitch: model= and "
                        "verifier= resolve to the same language model. Using the "
                        "same model as classifier and judge biases "
                        "verdicts toward the classifier's own errors. "
                        "Pass distinct models, or wrap the verifier as "
                        "JudgeSource(..., guard_against_same_llm=False) "
                        "if you explicitly accept the bias risk."
                    )

    # --- Labels ------------------------------------------------------------

    @property
    def labels(self) -> list[Label]:
        """Canonical label list (as :class:`Label` objects)."""
        return list(self._labels_raw)

    @labels.setter
    def labels(self, value: LabelsArg | None) -> None:
        self._labels_raw = _normalize_labels(value)
        self._label_index = {lbl.name: lbl for lbl in self._labels_raw}

    def _label_names(self) -> list[str]:
        return [label.name for label in self._labels_raw]

    def _find_label(self, name: Any) -> Label | None:
        # Dict lookup is O(1) vs a linear list scan; meaningful when
        # a switch has many labels (CLINC150 has 151). v1-readiness
        # §2 finding #23.
        if not isinstance(name, str):
            return None
        return self._label_index.get(name)

    # --- Public API --------------------------------------------------------

    def classify(self, input: Any) -> ClassificationResult:
        """Classify ``input`` — **pure**. Returns the decision; fires no actions.

        Routing depends on ``config.starting_phase``. Any ``Label.on=``
        callables registered on matching labels are **not** invoked
        here — that's :meth:`dispatch`'s job. ``classify()`` is safe to
        call from tests, benchmarks, dashboards, and other read-only
        observers that need the decision without the side effects.

        When ``config.auto_record`` is ``True`` (the default), this
        also appends a ``ClassificationRecord`` with
        ``outcome=Verdict.UNKNOWN`` to the outcome log, preserving the
        shadow observations captured during this call. Pass
        ``auto_record=False`` to suppress.
        """
        result = self._classify_impl(input)
        result._switch = self
        result._input = input
        # Hot-path optimization: when no verifier is configured we
        # skip the method-call overhead entirely. The verifier path
        # is opt-in; users without one should not pay for it.
        verified = (
            self._maybe_run_verifier(input, result) if self.config.verifier is not None else False
        )
        if not verified and self.config.auto_record:
            self._auto_log(input, result)
        try:
            self._telemetry.emit(
                "classify",
                {
                    "switch": self.name,
                    "phase": result.phase.value,
                    "source": result.source,
                    "confidence": result.confidence,
                    "verified": verified,
                },
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            pass
        return result

    def dispatch(self, input: Any) -> ClassificationResult:
        """Classify ``input`` and fire the matched label's ``on=`` action.

        The production verb: decide *and* act. Returns a
        :class:`ClassificationResult` with ``action_result`` /
        ``action_raised`` / ``action_elapsed_ms`` populated when the
        chosen label has an ``on=`` callable. Exceptions inside the
        action are captured (stringified into ``action_raised``), not
        propagated — the classification decision itself is never lost
        to a handler bug.

        When no label matches or the matched label has no ``on=``
        callable, behaves exactly like :meth:`classify` (but still
        emits a ``dispatch`` telemetry event so consumers can
        distinguish the two call sites). When ``config.auto_record``
        is ``True``, appends an UNKNOWN outcome record including the
        dispatched action's result.
        """
        result = self._classify_impl(input)
        result = self._maybe_dispatch(input, result)
        result._switch = self
        result._input = input
        verified = (
            self._maybe_run_verifier(input, result) if self.config.verifier is not None else False
        )
        if not verified and self.config.auto_record:
            self._auto_log(input, result)
        try:
            self._telemetry.emit(
                "dispatch",
                {
                    "switch": self.name,
                    "phase": result.phase.value,
                    "source": result.source,
                    "confidence": result.confidence,
                    "action_raised": result.action_raised,
                    "verified": verified,
                },
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            pass
        # Opt-in: re-raise the original handler exception AFTER the
        # outcome row + telemetry have landed, so the failure is visible
        # in the log even when the caller's ``except`` block fires.
        if self.config.propagate_action_exceptions and result._action_exception is not None:
            raise result._action_exception
        return result

    def _maybe_run_verifier(self, input: Any, result: ClassificationResult) -> bool:
        """Route the (input, label) pair through ``config.verifier``.

        Returns ``True`` when the verifier produced a verdict-bearing
        record (which supersedes the auto-record UNKNOWN log entry).
        Returns ``False`` when no verifier is configured, sampling
        skipped this call, or the verifier raised — in those cases
        the caller falls back to the existing ``auto_record``
        behavior.
        """
        verifier = self.config.verifier
        if verifier is None:
            return False
        if self.config.verifier_sample_rate < 1.0:
            import random

            if random.random() >= self.config.verifier_sample_rate:
                return False
        try:
            verdict = verifier.judge(input, result.label)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            # Verifier failure is absorbed — caller falls back to
            # auto_record's UNKNOWN row so we never silently drop
            # the observation.
            return False
        try:
            source_name = getattr(verifier, "source_name", "verifier")
            self.record_verdict(
                input=input,
                label=result.label,
                outcome=verdict.value,
                source=source_name,
                confidence=result.confidence,
                _result_ctx=result,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            return False
        return True

    def _auto_log(self, input: Any, result: ClassificationResult) -> None:
        """Append an UNKNOWN-outcome record from the just-completed call.

        Pulls shadow observations from the result itself (populated
        by ``_classify_impl``), not from any per-switch instance
        stash — which is what keeps concurrent classifies from
        cross-contaminating each other's records.
        """
        record = ClassificationRecord(
            timestamp=time.time(),
            input=input,
            label=result.label,
            outcome=Verdict.UNKNOWN.value,
            source=result.source,
            confidence=result.confidence,
            rule_output=result._rule_output,
            model_output=result._model_output,
            model_confidence=result._model_confidence,
            ml_output=result._ml_output,
            ml_confidence=result._ml_confidence,
            action_result=result.action_result,
            action_raised=result.action_raised,
            action_elapsed_ms=result.action_elapsed_ms,
        )
        try:
            self._storage.append_record(self.name, record)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            # Storage failure must not break classification.
            pass

    @contextlib.contextmanager
    def verdict_for(self, input: Any):
        """Context manager yielding a verdict holder for ``input``.

        Classifies ``input`` on entry and yields an object with
        ``.result`` (the :class:`ClassificationResult`) plus
        ``.correct()`` / ``.incorrect()`` / ``.unknown()`` methods.
        If the block exits without a verdict being marked, defaults
        to UNKNOWN so an incident log always has a trailing state.

        Usage::

            with rule.verdict_for(ticket) as v:
                try:
                    do_downstream(v.result.label)
                    v.correct()
                except HandlerError:
                    v.incorrect()
        """
        result = self.classify(input)
        holder = _VerdictHolder(switch=self, input=input, result=result)
        try:
            yield holder
        finally:
            if not holder._recorded:
                holder.unknown()

    def _maybe_dispatch(self, input: Any, result: ClassificationResult) -> ClassificationResult:
        """Fire the chosen label's ``on=`` action, if any.

        Captures failures rather than propagating; the caller sees
        ``action_raised`` populated. Timing is recorded on success and
        failure. The shadow observations threaded on ``result`` are
        preserved through to the new result so ``_auto_log`` still
        sees them.
        """
        label = self._find_label(result.label)
        if label is None or label.on is None:
            return result

        start = time.perf_counter()
        action_result: Any = None
        action_raised: str | None = None
        action_exception: BaseException | None = None
        try:
            action_result = label.on(input)
        except (KeyboardInterrupt, SystemExit):
            # System-level exits propagate regardless of the
            # ``propagate_action_exceptions`` knob.
            raise
        except BaseException as e:
            action_raised = f"{type(e).__name__}: {e}"
            action_exception = e
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        new_result = ClassificationResult(
            label=result.label,
            source=result.source,
            confidence=result.confidence,
            phase=result.phase,
            action_result=action_result,
            action_raised=action_raised,
            action_elapsed_ms=elapsed_ms,
        )
        # Re-thread shadow observations through the dispatch-returned
        # result so record_verdict / auto_log still sees them.
        new_result._rule_output = result._rule_output
        new_result._model_output = result._model_output
        new_result._model_confidence = result._model_confidence
        new_result._ml_output = result._ml_output
        new_result._ml_confidence = result._ml_confidence
        # Stash the live exception for ``dispatch`` / ``adispatch`` to
        # consult after telemetry + auto_log have run, so the storage
        # row with ``action_raised`` populated lands BEFORE any re-raise.
        new_result._action_exception = action_exception
        return new_result

    def _classify_impl(self, input: Any) -> ClassificationResult:
        phase = self.phase()
        # Runtime re-check of the paper §7.1 architectural guarantee.
        # Construction-time checks catch the common case, but
        # ``config`` is a mutable dataclass — users (or bugs) can
        # raise the phase after construction. The rule-floor promise
        # must survive that. Refuse to serve ML_PRIMARY when
        # safety_critical is set, regardless of how the phase got here.
        if self.config.safety_critical and phase is Phase.ML_PRIMARY:
            raise RuntimeError(
                f"switch {self.name!r} is safety_critical=True but phase "
                f"is ML_PRIMARY. The rule floor cannot be removed "
                f"architecturally (paper §7.1); this state should be "
                f"unreachable. Check for direct mutation of "
                f"config.starting_phase."
            )
        rule_output = self._rule(input)

        def _result(
            label: Any,
            source: str,
            confidence: float,
            *,
            model_output: Any = None,
            model_confidence: float | None = None,
            ml_output: Any = None,
            ml_confidence: float | None = None,
        ) -> ClassificationResult:
            r = ClassificationResult(
                label=label,
                source=source,
                confidence=_clamp_conf(confidence) or 0.0,
                phase=phase,
            )
            r._rule_output = rule_output
            r._model_output = model_output
            r._model_confidence = _clamp_conf(model_confidence)
            r._ml_output = ml_output
            r._ml_confidence = _clamp_conf(ml_confidence)
            return r

        if phase is Phase.RULE:
            return _result(rule_output, "rule", 1.0)

        if phase is Phase.MODEL_SHADOW:
            if self._model is None:
                raise ValueError(
                    f"switch {self.name!r} is in phase {phase.value} but no "
                    "model classifier was provided"
                )
            # Shadow: run the language model for observation but never let failure
            # propagate — the rule is the user-visible decision.
            model_output: Any = None
            model_confidence: float | None = None
            try:
                pred = self._model.classify(input, self._label_names())
                model_output = pred.label
                model_confidence = float(pred.confidence)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                pass
            return _result(
                rule_output,
                "rule",
                1.0,
                model_output=model_output,
                model_confidence=model_confidence,
            )

        if phase is Phase.MODEL_PRIMARY:
            if self._model is None:
                raise ValueError(
                    f"switch {self.name!r} is in phase {phase.value} but no "
                    "model classifier was provided"
                )
            try:
                pred = self._model.classify(input, self._label_names())
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                return _result(rule_output, "rule_fallback", 1.0)
            # Empty / whitespace-only labels are not a decision: a
            # misbehaving adapter can return label="" with high
            # confidence; treat that as no-decision and fall back to
            # the rule (issue #138).
            if not _is_real_label(pred.label):
                return _result(
                    rule_output,
                    "rule_fallback",
                    1.0,
                    model_output=pred.label,
                    model_confidence=float(pred.confidence),
                )
            if float(pred.confidence) < self.config.confidence_threshold:
                return _result(
                    rule_output,
                    "rule_fallback",
                    1.0,
                    model_output=pred.label,
                    model_confidence=float(pred.confidence),
                )
            return _result(
                pred.label,
                "model",
                float(pred.confidence),
                model_output=pred.label,
                model_confidence=float(pred.confidence),
            )

        if phase is Phase.ML_SHADOW:
            self._ensure_ml_head(phase)
            # Primary decision path at Phase 3 mirrors MODEL_PRIMARY when an
            # language model is configured, else falls to rule. ML runs only in shadow.
            primary = self._phase_primary_decision(input, rule_output, phase)
            ml_output: Any = None
            ml_confidence: float | None = None
            try:
                ml_pred = self._ml_head.predict(input, self._label_names())
                ml_output = ml_pred.label
                ml_confidence = float(ml_pred.confidence)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                pass
            primary._ml_output = ml_output
            primary._ml_confidence = ml_confidence
            return primary

        if phase is Phase.ML_WITH_FALLBACK:
            self._ensure_ml_head(phase)
            ml_output: Any = None
            ml_confidence: float | None = None
            ml_failed = False
            try:
                ml_pred = self._ml_head.predict(input, self._label_names())
                ml_output = ml_pred.label
                ml_confidence = float(ml_pred.confidence)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                ml_failed = True

            # Predecessor-cascade fallback (paper §3.1): on low-confidence or
            # failed H, route through the predecessor phase's logic
            # (MODEL_PRIMARY: M then R) rather than collapsing to R. The H
            # tier sits *on top of* the cascade we earned at P2.
            if (
                ml_failed
                or ml_confidence is None
                or ml_confidence < self.config.confidence_threshold
                or not _is_real_label(ml_output)
            ):
                cascaded = self._phase_primary_decision(input, rule_output, phase)
                cascaded._ml_output = ml_output
                cascaded._ml_confidence = ml_confidence
                # Cascade landed at R because the higher tier (H) was
                # below threshold or failed; mark as rule_fallback even
                # if M was absent (no M attempt was made, but the
                # cascade still bottomed out due to upstream uncertainty).
                if cascaded.source == "rule":
                    cascaded = ClassificationResult(
                        label=cascaded.label,
                        source="rule_fallback",
                        confidence=cascaded.confidence,
                        phase=cascaded.phase,
                    )
                    cascaded._rule_output = rule_output
                    cascaded._ml_output = ml_output
                    cascaded._ml_confidence = ml_confidence
                return cascaded
            return _result(
                ml_pred.label,
                "ml",
                ml_confidence,
                ml_output=ml_output,
                ml_confidence=ml_confidence,
            )

        if phase is Phase.ML_PRIMARY:
            self._ensure_ml_head(phase)
            # Lock the breaker check-and-call sequence so the first
            # failure trips the circuit and subsequent callers
            # short-circuit to the rule rather than stampeding the
            # broken ML head (v1-readiness.md §2 finding #16, F2).
            with self._lock:
                if self._circuit_tripped:
                    return _result(rule_output, "rule_fallback", 1.0)
                try:
                    ml_pred = self._ml_head.predict(input, self._label_names())
                except (KeyboardInterrupt, SystemExit):
                    raise
                except BaseException:
                    self._circuit_tripped = True
                    self._save_breaker_state()
                    return _result(rule_output, "rule_fallback", 1.0)
                # Empty / whitespace-only labels are not a decision —
                # treat as fallback rather than emit "" downstream
                # (issue #138).
                if not _is_real_label(ml_pred.label):
                    return _result(
                        rule_output,
                        "rule_fallback",
                        1.0,
                        ml_output=ml_pred.label,
                        ml_confidence=float(ml_pred.confidence),
                    )
                return _result(
                    ml_pred.label,
                    "ml",
                    float(ml_pred.confidence),
                    ml_output=ml_pred.label,
                    ml_confidence=float(ml_pred.confidence),
                )

        # Unreachable — exhaustive enum handled above.
        return _result(rule_output, "rule", 1.0)

    def _phase_primary_decision(
        self, input: Any, rule_output: Any, phase: Phase
    ) -> ClassificationResult:
        """Primary decision for phases where an ML head runs in shadow.

        Routes through the language model when configured (MODEL_PRIMARY semantics),
        otherwise falls back to the rule. Never touches the ML head —
        that's the shadow layer's job.
        """

        def _r(
            label: Any,
            source: str,
            confidence: float,
            *,
            model_output: Any = None,
            model_confidence: float | None = None,
        ) -> ClassificationResult:
            r = ClassificationResult(
                label=label,
                source=source,
                confidence=confidence,
                phase=phase,
            )
            r._rule_output = rule_output
            r._model_output = model_output
            r._model_confidence = model_confidence
            return r

        if self._model is None:
            return _r(rule_output, "rule", 1.0)
        try:
            pred = self._model.classify(input, self._label_names())
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            return _r(rule_output, "rule_fallback", 1.0)
        # Empty / whitespace-only labels are not a decision (issue #138).
        if not _is_real_label(pred.label):
            return _r(
                rule_output,
                "rule_fallback",
                1.0,
                model_output=pred.label,
                model_confidence=float(pred.confidence),
            )
        if float(pred.confidence) < self.config.confidence_threshold:
            return _r(
                rule_output,
                "rule_fallback",
                1.0,
                model_output=pred.label,
                model_confidence=float(pred.confidence),
            )
        return _r(
            pred.label,
            "model",
            float(pred.confidence),
            model_output=pred.label,
            model_confidence=float(pred.confidence),
        )

    def record_verdict(
        self,
        *,
        input: Any,
        label: Any,
        outcome: str,
        source: str = "rule",
        confidence: float = 1.0,
        _result_ctx: ClassificationResult | None = None,
    ) -> None:
        """Append a labeled outcome to the storage log.

        ``label`` is the decision the switch returned (or that the
        caller assigned). ``outcome`` is the :class:`Verdict` — did
        that decision match ground truth?

        When this is called via a :class:`ClassificationResult`'s
        ``mark_*()`` / ``verdict_for`` path, the result's captured
        shadow observations (rule output, model prediction, ML
        prediction) are threaded through to the persisted record
        automatically. When called directly on the switch (e.g.
        from a webhook with ``record_verdict(input=..., label=...,
        outcome=...)``), the switch has no reliable way to pair the
        verdict with its originating classify — so no shadow
        observations are attached. Use the result-aware path when
        per-call paired data is load-bearing.
        """
        if outcome not in _VERDICT_VALUES:
            raise ValueError(f"outcome must be one of {sorted(_VERDICT_VALUES)}; got {outcome!r}")
        # Pull shadow observations from the paired result when
        # available; otherwise fall back to a minimal record with
        # just the user-supplied fields. No instance-level stash —
        # that was the source of the cross-contamination bug.
        if _result_ctx is not None:
            rule_output = _result_ctx._rule_output
            if rule_output is None and source == "rule":
                rule_output = label
            model_output = _result_ctx._model_output
            model_confidence = _result_ctx._model_confidence
            ml_output = _result_ctx._ml_output
            ml_confidence = _result_ctx._ml_confidence
            action_result = _result_ctx.action_result
            action_raised = _result_ctx.action_raised
            action_elapsed_ms = _result_ctx.action_elapsed_ms
        else:
            rule_output = label if source == "rule" else None
            model_output = None
            model_confidence = None
            ml_output = None
            ml_confidence = None
            action_result = None
            action_raised = None
            action_elapsed_ms = None

        record = ClassificationRecord(
            timestamp=time.time(),
            input=input,
            label=label,
            outcome=outcome,
            source=source,
            confidence=confidence,
            rule_output=rule_output,
            model_output=model_output,
            model_confidence=model_confidence,
            ml_output=ml_output,
            ml_confidence=ml_confidence,
            action_result=action_result,
            action_raised=action_raised,
            action_elapsed_ms=action_elapsed_ms,
        )
        self._storage.append_record(self.name, record)
        if self.config.on_verdict is not None:
            # User-supplied hook for mirroring verdicts (webhooks,
            # external audit stores, metrics). Failures never break
            # record_verdict.
            try:
                self.config.on_verdict(record)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                pass
        try:
            phase_letter = f"P{_PHASE_ORDER.get(self.config.starting_phase, -1)}"
            if phase_letter == "P-1":
                phase_letter = None  # unreachable on a healthy switch; safety net
            self._telemetry.emit(
                "outcome",
                {
                    "switch": self.name,
                    "outcome": outcome,
                    "source": source,
                    "phase": phase_letter,
                    "label": label,
                    "rule_output": rule_output,
                    "model_output": model_output,
                    "ml_output": ml_output,
                    # Pre-computed per-classifier correctness (rule / model /
                    # ml). Each is True/False when this classifier's
                    # prediction matches the chosen label AND we have a
                    # verdict for that label; None otherwise. See
                    # :func:`_per_classifier_correct` for the full
                    # decision table — the cloud pipe consumes these
                    # directly so we don't ship raw predictions over the
                    # wire.
                    "rule_correct": _per_classifier_correct(rule_output, label, outcome),
                    "model_correct": _per_classifier_correct(model_output, label, outcome),
                    "ml_correct": _per_classifier_correct(ml_output, label, outcome),
                },
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            pass

        # Auto-evaluate: every ``auto_advance_interval`` recorded
        # predictions, ask the configured gates whether the phase
        # should change. We check the drift gate first (demote takes
        # precedence over advance because a drift signal is a safety
        # regression that should not be masked by an advance-eligible
        # signal). Counter increments are locked so concurrent
        # record_verdicts can't race on read-modify-write (v1
        # finding #16). Gate refusals and exceptions never break
        # record_verdict.
        if self.config.auto_advance or self.config.auto_demote:
            should_check = False
            with self._lock:
                self._records_since_advance_check += 1
                self._records_since_phase_change += 1
                if (
                    self._records_since_advance_check >= self.config.auto_advance_interval
                    and self._records_since_phase_change >= self.config.phase_cooldown_records
                ):
                    self._records_since_advance_check = 0
                    should_check = True
            if should_check:
                # Drift first. If it fires, demote and skip the advance
                # check this cycle.
                demoted = False
                if self.config.auto_demote and self.config.drift_gate is not None:
                    try:
                        demoted = self._maybe_auto_demote()
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except BaseException:
                        demoted = False
                if not demoted and self.config.auto_advance:
                    try:
                        self.advance(_auto=True)
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except BaseException:
                        pass

    def _maybe_auto_demote(self) -> bool:
        """Run the configured ``drift_gate`` and demote if it fires.

        Returns ``True`` iff the gate said the rule was the better
        target and the lifecycle stepped one phase back. Internal
        helper for the auto-evaluate loop in ``record_verdict``;
        operators call :meth:`demote` directly for manual demotion.
        """
        with self._lock:
            current = self.config.starting_phase
        if current is Phase.RULE:
            return False  # nothing below the rule
        records = self._storage.load_records(self.name)
        decision = self.config.drift_gate.evaluate(records, current, Phase.RULE)
        if not decision.target_better:
            return False
        # Drift confirmed. Route through demote() so the audit chain
        # captures the same telemetry shape as a manual demotion.
        rationale = (
            f"auto-demote: drift gate "
            f"({type(self.config.drift_gate).__name__}) reports rule reliably "
            f"better than {current.name} on accumulated evidence"
        )
        self.demote(reason=rationale, _auto=True, _decision=decision)
        return True

    def phase(self) -> Phase:
        """Current lifecycle phase.

        Reads ``config.starting_phase``. :meth:`advance` mutates this
        state when the configured gate says the next phase is earned.
        """
        return self.config.starting_phase

    def phase_limit(self) -> Phase:
        """Ceiling on this switch's phase. :meth:`advance` refuses to exceed."""
        return self.config.phase_limit

    def advance(self, *, _auto: bool = False) -> Any:
        """Evaluate the configured gate and advance the phase on pass.

        Returns a :class:`postrule.gates.GateDecision` regardless of
        whether the phase moved — operators and audit trails want to
        see the rationale either way. When ``decision.target_better`` is
        ``True``, ``config.starting_phase`` is mutated up by one
        phase in the lifecycle and an ``advance`` telemetry event is
        emitted.

        The gate is ``config.gate`` (defaults to
        :class:`postrule.gates.McNemarGate`). Construction-time
        invariants still hold: ``advance`` refuses to exceed
        ``phase_limit`` and never touches a terminal phase.

        **Automatic scheduling.** By default,
        :meth:`record_verdict` calls ``advance`` every
        ``config.auto_advance_interval`` records. Set
        ``auto_advance=False`` on the switch to disable and call
        this method from your own cron / ops workflow. Manual calls
        still work when auto-advance is on — use them for on-demand
        probes or to force an evaluation ahead of schedule.

        ``_auto`` is an internal flag tagging the telemetry event;
        it is not part of the stable API.
        """
        from postrule.gates import GateDecision, next_phase

        # Serialize the read-log / evaluate-gate / mutate-phase
        # sequence against concurrent classifies (which read
        # starting_phase) and other advance() callers.
        with self._lock:
            current = self.config.starting_phase
            target = next_phase(current)
            if target is None:
                return GateDecision(
                    target_better=False,
                    rationale=f"already at terminal phase {current.name}",
                )
            if _PHASE_ORDER[target] > _PHASE_ORDER[self.config.phase_limit]:
                return GateDecision(
                    target_better=False,
                    rationale=(
                        f"target phase {target.name} exceeds phase_limit "
                        f"{self.config.phase_limit.name}"
                    ),
                )
            if self.config.safety_critical and target is Phase.ML_PRIMARY:
                # safety_critical refuses ML_PRIMARY on the hot path per
                # paper §7.1, regardless of gate evidence. Belt + suspenders
                # on top of phase_limit — if anyone ever widens that cap,
                # this check still enforces the architectural guarantee.
                return GateDecision(
                    target_better=False,
                    rationale=(
                        "safety_critical=True refuses advancement to ML_PRIMARY (paper §7.1)"
                    ),
                )

            records = self._storage.load_records(self.name)
            decision = self.config.gate.evaluate(records, current, target)

            if decision.target_better:
                self.config.starting_phase = target
                # Reset the phase-change cooldown counter so the
                # auto-check loop suppresses both gates until fresh
                # paired-correctness data accumulates.
                self._records_since_phase_change = 0

        if decision.target_better:
            try:
                self._telemetry.emit(
                    "advance",
                    {
                        "switch": self.name,
                        "from": current.value,
                        "to": target.value,
                        "rationale": decision.rationale,
                        "p_value": decision.p_value,
                        "paired_sample_size": decision.paired_sample_size,
                        "current_accuracy": decision.current_accuracy,
                        "target_accuracy": decision.target_accuracy,
                        "auto": _auto,
                    },
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                pass

        # Persist the ML head once it joins the routing graph so the
        # switch survives restart in trained state. *Outside* the
        # phase-mutation lock so a slow pickle.dumps + disk write does
        # not stall concurrent classify() callers waiting for the
        # lock. The head-state snapshot acquires its own (separate)
        # head_lock; see ``_save_head_state``.
        if decision.target_better:
            self._save_head_state()

        return decision

    def persist_head(self) -> None:
        """Manually checkpoint the ML head to disk.

        Call after fitting the head externally to ensure restart
        survival. No-op when ``persist=False`` was passed at
        construction or when the head doesn't implement
        ``state_bytes`` / ``load_state``.
        """
        self._save_head_state()

    def demote(self, *, reason: str, _auto: bool = False, _decision: Any = None) -> Any:
        """Step the lifecycle phase one slot back toward the rule floor.

        The symmetric counterpart of :meth:`advance`. Where ``advance``
        is gate-driven (the McNemar gate must say "yes" before the
        phase moves up), ``demote`` is operator-driven: it mutates
        ``config.starting_phase`` to the previous phase unconditionally
        with ``reason`` recorded in telemetry. The auto-demote loop
        uses the configured :class:`DriftGate` to drive the same path
        autonomously; ``_decision`` is the internal hand-off point for
        that loop.

        Parameters:
            reason: non-empty operator-facing string describing why
                the demotion was performed. Required by API contract;
                the audit chain relies on it.
            _auto: internal flag tagging the telemetry event when the
                auto-demote loop fires. Not part of the stable API.
            _decision: internal hand-off from the auto-demote loop;
                carries the :class:`GateDecision` produced by the
                drift gate so its rationale + p-value land in the
                audit log unchanged.

        Returns a :class:`postrule.gates.GateDecision`. ``target_better=True``
        on the returned decision means "phase moved one step back" (the
        rule was the better target). ``target_better=False`` indicates
        the request was a no-op (already at :attr:`Phase.RULE`).

        ``safety_critical=True`` does NOT block demotion — that flag
        caps the forward ceiling; demoting strengthens the safety
        floor and is always permitted.
        """
        if not reason or not reason.strip():
            raise ValueError("demote(reason=...) requires a non-empty reason")

        from postrule.gates import GateDecision, prev_phase

        with self._lock:
            current = self.config.starting_phase
            target = prev_phase(current)
            if target is None:
                return GateDecision(
                    target_better=False,
                    rationale=(f"already at lifecycle floor {current.name}; nothing to demote"),
                )

            self.config.starting_phase = target
            # Reset the phase-change cooldown counter for symmetry with
            # advance(); the auto-check loop suppresses both gates until
            # fresh paired-correctness data accumulates.
            self._records_since_phase_change = 0

        # Build the audit-facing decision. If the auto-demote loop
        # supplied a gate decision, preserve its statistical fields and
        # extend the rationale with the operator-facing reason. Manual
        # demote synthesizes a fresh decision with the operator reason.
        if _decision is not None:
            decision = GateDecision(
                target_better=True,
                rationale=f"{_decision.rationale}; reason: {reason}",
                p_value=_decision.p_value,
                paired_sample_size=_decision.paired_sample_size,
                current_accuracy=_decision.current_accuracy,
                target_accuracy=_decision.target_accuracy,
            )
        else:
            decision = GateDecision(
                target_better=True,
                rationale=(f"manual demote {current.name} → {target.name}; reason: {reason}"),
            )

        try:
            self._telemetry.emit(
                "demote",
                {
                    "switch": self.name,
                    "from": current.value,
                    "to": target.value,
                    "rationale": decision.rationale,
                    "reason": reason,
                    "p_value": decision.p_value,
                    "paired_sample_size": decision.paired_sample_size,
                    "current_accuracy": decision.current_accuracy,
                    "target_accuracy": decision.target_accuracy,
                    "auto": _auto,
                },
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            pass

        return decision

    def status(self) -> SwitchStatus:
        """Return a :class:`SwitchStatus` snapshot."""
        outcomes = self._storage.load_records(self.name)
        total = len(outcomes)
        correct = sum(1 for r in outcomes if r.outcome == Verdict.CORRECT.value)
        incorrect = sum(1 for r in outcomes if r.outcome == Verdict.INCORRECT.value)

        shadow_rate: float | None = None
        shadow_obs = [
            r for r in outcomes if r.model_output is not None and r.rule_output is not None
        ]
        if shadow_obs:
            agreements = sum(1 for r in shadow_obs if r.model_output == r.rule_output)
            shadow_rate = agreements / len(shadow_obs)

        ml_rate: float | None = None
        ml_obs = [r for r in outcomes if r.ml_output is not None]
        if ml_obs:
            # Compare ML against whatever the user-visible decision was
            # (output), which is the most load-bearing definition of
            # "agreement" for Phase 3+ transition math.
            ml_agreements = sum(1 for r in ml_obs if r.ml_output == r.label)
            ml_rate = ml_agreements / len(ml_obs)

        version = self._ml_head.model_version() if self._ml_head is not None else None

        return SwitchStatus(
            name=self.name,
            phase=self.phase(),
            outcomes_total=total,
            outcomes_correct=correct,
            outcomes_incorrect=incorrect,
            model_version=version,
            shadow_agreement_rate=shadow_rate,
            ml_agreement_rate=ml_rate,
            circuit_breaker_tripped=self._circuit_tripped,
        )

    # --- Bulk ingestion + reviewer round-trip -----------------------------

    def bulk_record_verdicts(
        self,
        verdicts: Iterable[BulkVerdict],
        /,
    ) -> BulkVerdictSummary:
        """Append many verdicts in one pass.

        Each entry is a :class:`BulkVerdict`. Storage failures on
        individual rows are absorbed so a single flaky record
        doesn't poison the whole batch — they're counted in
        ``summary.failed``. Auto-advance is deferred: at most one
        gate evaluation fires per ``bulk_record_verdicts`` call,
        not N evaluations on every interval boundary the batch
        crosses.

        Intended for cold-start preload (feed historical labeled
        data to seed the outcome log before going live), periodic
        reviewer-queue ingestion (``apply_reviews`` is built on
        this), and verdict-source-driven pipelines
        (``bulk_record_verdicts_from_source``).
        """
        summary = BulkVerdictSummary()
        # Preserve the currently-enabled auto_advance flag, then
        # disable it for the duration of the batch. One advance()
        # call at the end amortizes the gate walk over the whole
        # batch rather than firing mid-iteration on every interval.
        prior_auto_advance = self.config.auto_advance
        self.config.auto_advance = False
        try:
            for v in verdicts:
                summary.total += 1
                try:
                    self.record_verdict(
                        input=v.input,
                        label=v.label,
                        outcome=v.outcome,
                        source=v.source,
                        confidence=v.confidence,
                    )
                    summary.recorded += 1
                except (KeyboardInterrupt, SystemExit):
                    raise
                except BaseException:
                    summary.failed += 1
        finally:
            self.config.auto_advance = prior_auto_advance
        if prior_auto_advance and summary.recorded:
            # End-of-batch gate probe, regardless of how far the
            # counter moved during the batch. Reset the counter so
            # the next N record_verdict calls start a fresh interval.
            with self._lock:
                self._records_since_advance_check = 0
            try:
                summary.auto_advance_decision = self.advance(_auto=True)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                pass
        return summary

    def bulk_record_verdicts_from_source(
        self,
        inputs: Iterable[Any],
        source: Any,  # VerdictSource — typed in postrule.verdicts
        /,
    ) -> BulkVerdictSummary:
        """Classify each input, judge via ``source``, record verdicts.

        The composition that turns "I have 1,000 production inputs
        and a VerdictSource" into "the outcome log is seeded with
        paired observations and the right source stamps." Classify
        happens through the switch (so phase-appropriate routing +
        shadow capture applies); the verdict source judges the
        resulting label; the verdict is recorded with the source's
        stable ``source_name`` on the record's ``source`` field
        for audit-chain filtering.
        """
        summary = BulkVerdictSummary()
        prior_auto_advance = self.config.auto_advance
        self.config.auto_advance = False
        try:
            for input in inputs:
                summary.total += 1
                try:
                    result = self.classify(input)
                    verdict = source.judge(input, result.label)
                    self.record_verdict(
                        input=input,
                        label=result.label,
                        outcome=verdict.value,
                        source=source.source_name,
                        confidence=result.confidence,
                        _result_ctx=result,
                    )
                    summary.recorded += 1
                except (KeyboardInterrupt, SystemExit):
                    raise
                except BaseException:
                    summary.failed += 1
        finally:
            self.config.auto_advance = prior_auto_advance
        if prior_auto_advance and summary.recorded:
            with self._lock:
                self._records_since_advance_check = 0
            try:
                summary.auto_advance_decision = self.advance(_auto=True)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                pass
        return summary

    def export_for_review(
        self,
        *,
        limit: int | None = None,
        since: float | None = None,
        filter: Callable[[ClassificationRecord], bool] | None = None,
    ) -> list[dict]:
        """Produce a reviewer-facing queue of UNKNOWN-outcome records.

        Returns a list of plain dicts (JSON-serializable, no
        framework types) that a human-review UI, a Redis/SQS queue,
        or a batch-CSV export can consume directly. Each dict
        carries ``input_hash`` so :meth:`apply_reviews` can
        correlate reviewer annotations back to the originating row.

        ``limit`` caps the returned list size; ``since`` filters by
        record timestamp (POSIX seconds); ``filter`` is a
        predicate the caller applies on top of the UNKNOWN + since
        filters.
        """
        out: list[dict] = []
        for r in self._storage.load_records(self.name):
            if r.outcome != Verdict.UNKNOWN.value:
                continue
            if since is not None and r.timestamp < since:
                continue
            if filter is not None and not filter(r):
                continue
            out.append(
                {
                    "input_hash": _input_hash(r.input),
                    "input": r.input,
                    "classified_label": r.label,
                    "classified_source": r.source,
                    "classified_confidence": r.confidence,
                    "timestamp": r.timestamp,
                    "rule_output": r.rule_output,
                    "model_output": r.model_output,
                    "ml_output": r.ml_output,
                }
            )
            if limit is not None and len(out) >= limit:
                break
        return out

    def apply_reviews(
        self,
        reviews: Iterable[dict],
        /,
    ) -> BulkVerdictSummary:
        """Ingest reviewer-annotated records back into the outcome log.

        Each review dict must carry ``input_hash`` (from
        :meth:`export_for_review`), ``outcome`` (a :class:`Verdict`
        value), and either ``input`` or enough context for the
        caller's own records. Optional: ``label`` override
        (defaults to the classified label), ``source``
        (defaults to ``"human-reviewer"``), ``confidence``.

        Reviews for input_hashes that don't match any UNKNOWN row
        in the log are skipped — they count toward
        ``summary.failed``. Matched reviews append a new
        verdict-bearing record to the log (additive, not
        update-in-place); the original UNKNOWN row stays put for
        the audit trail.
        """
        by_hash: dict[str, ClassificationRecord] = {}
        for r in self._storage.load_records(self.name):
            if r.outcome == Verdict.UNKNOWN.value:
                by_hash[_input_hash(r.input)] = r

        batch: list[BulkVerdict] = []
        unmatched = 0
        for review in reviews:
            h = review.get("input_hash")
            if h is None or h not in by_hash:
                unmatched += 1
                continue
            original = by_hash[h]
            batch.append(
                BulkVerdict(
                    input=original.input,
                    label=review.get("label", original.label),
                    outcome=review["outcome"],
                    source=review.get("source", "human-reviewer"),
                    confidence=float(review.get("confidence", 1.0)),
                )
            )
        summary = self.bulk_record_verdicts(batch)
        summary.failed += unmatched
        summary.total += unmatched
        return summary

    def reset_circuit_breaker(self, *, operator: str | None = None) -> None:
        """Clear a tripped circuit breaker and allow ML decisions again.

        The breaker trips automatically on ML failures in Phase 5
        (ML_PRIMARY); calling this signals that the operator has
        investigated and is ready to resume ML-primary decisions.

        ``operator`` is an opaque string stored on the emitted
        telemetry event and in the operator-action record — useful
        for audit trails. It is provenance, not authorization:
        Postrule does not verify the string; any access-control
        gating happens in the calling layer. See v1-readiness.md
        §2 finding #21.
        """
        with self._lock:
            self._circuit_tripped = False
            self._save_breaker_state()
        try:
            self._telemetry.emit(
                "reset_circuit_breaker",
                {"switch": self.name, "operator": operator},
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            pass

    # --- Breaker-state persistence (paper §7.1 survives restart) ---------

    def _breaker_path(self) -> Path | None:
        """Path to the breaker-state sidecar file, or ``None`` if unsupported.

        Breaker persistence is enabled when ``persist=True`` was
        passed at construction — see v1-readiness.md §5 D2.
        """
        if not getattr(self, "_persist", False):
            return None
        return Path("runtime") / "postrule" / self.name / ".breaker"

    def _save_breaker_state(self) -> None:
        """Persist ``_circuit_tripped`` to disk when configured.

        Called from the lock holders that mutate the breaker
        (``_classify_impl`` ML_PRIMARY branch, ``reset_circuit_breaker``).
        A failure here never propagates — persistence is best-effort;
        crashing the classifier over a sidecar-file write is a worse
        outcome than losing a restart-survival on one trip.
        """
        path = self._breaker_path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("1" if self._circuit_tripped else "0")
        except OSError:
            pass

    def _load_breaker_state(self) -> None:
        """Rehydrate ``_circuit_tripped`` from disk at construction.

        Called from ``__init__`` only when ``persist=True``. A
        missing or unreadable file leaves the breaker untripped
        (the safe default).
        """
        path = self._breaker_path()
        if path is None:
            return
        try:
            raw = path.read_text().strip()
        except (OSError, FileNotFoundError):
            return
        if raw == "1":
            self._circuit_tripped = True

    # --- ML head state persistence (avoids re-fit on every restart) ------

    def _head_path(self) -> Path | None:
        """Path to the ML-head state sidecar file, or ``None`` if unsupported.

        Head persistence is enabled when ``persist=True`` AND the
        configured ``ml_head`` implements both ``state_bytes`` and
        ``load_state``. Heads without these methods fall back to
        refit-from-log on first prediction.
        """
        if not getattr(self, "_persist", False):
            return None
        head = self._ml_head
        if head is None:
            return None
        if not hasattr(head, "state_bytes") or not hasattr(head, "load_state"):
            return None
        return Path("runtime") / "postrule" / self.name / ".head"

    def _save_head_state(self) -> None:
        """Persist trained ML-head state to disk.

        Thread safety: the snapshot (``state_bytes``) is taken under
        ``self._head_lock`` to serialize against concurrent fits or
        loads. The disk write happens *after* the lock is released so
        a slow pickle.dumps + filesystem write does not block other
        threads from fitting or saving. classify() / predict() are
        unaffected — they read ``self._ml_head._pipeline`` directly
        and do not touch the head lock.

        Best-effort. A failure here never propagates: a head that
        can't checkpoint is degraded but functional; a head that
        crashes the classifier is broken. The next restart re-fits
        from the verdict log if the sidecar is missing or unreadable.
        """
        path = self._head_path()
        if path is None:
            return
        try:
            with self._head_lock:
                blob = self._ml_head.state_bytes()
        except (NotImplementedError, AttributeError):
            return
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(blob)
            tmp.replace(path)
        except OSError:
            pass

    def _load_head_state(self) -> None:
        """Rehydrate trained ML-head state from disk.

        Acquires ``self._head_lock`` to serialize against concurrent
        fits / saves. Reading the file from disk happens before the
        lock is taken so multi-second filesystem reads do not block
        other threads.

        A missing or unreadable file leaves the head untrained (the
        safe default; the next ``fit`` from the verdict log restores
        readiness).
        """
        path = self._head_path()
        if path is None:
            return
        try:
            blob = path.read_bytes()
        except (OSError, FileNotFoundError):
            return
        try:
            with self._head_lock:
                self._ml_head.load_state(blob)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            # Corrupt blob, version mismatch, etc. — discard and let
            # the head re-fit from the log.
            return

    def _ensure_ml_head(self, phase: Phase) -> None:
        """Ensure ``self._ml_head`` is set, consulting head_strategy if needed.

        Lazy: the strategy is consulted on first access from an
        ML-using phase, never at construction. Cached afterward.
        Thread-safe via ``self._head_lock``: concurrent classifies
        call ``select`` at most once.

        Raises ``ValueError`` if no head was provided AND no strategy
        was configured (matches the pre-strategy behavior).
        """
        if self._ml_head is not None:
            return
        if self._head_strategy is None:
            raise ValueError(
                f"switch {self.name!r} is in phase {phase.value} but no "
                f"ml_head was provided (and no head_strategy to pick one)"
            )
        with self._head_lock:
            if self._ml_head is not None:
                return  # another thread populated it while we waited
            records = self._storage.load_records(self.name)
            self._ml_head = self._head_strategy.select(records)

    def _lazy_load_head_in_background(self) -> None:
        """Load persisted head state on a background thread.

        Started by ``__init__`` when ``persist=True`` and the head
        supports ``load_state``. Construction returns immediately;
        callers that need trained state before the first request use
        ``wait_until_head_loaded(timeout)``.
        """
        try:
            self._load_head_state()
        finally:
            self._head_loaded.set()

    def wait_until_head_loaded(self, timeout: float | None = None) -> bool:
        """Block until the persisted ML head has finished loading.

        Useful right after construction when the next request must
        see trained state. Returns ``True`` if loaded (or if there
        was nothing to load), ``False`` if the timeout elapsed first.
        Always returns ``True`` immediately on switches without
        ``persist=True`` or without a ``load_state``-capable head.
        """
        return self._head_loaded.wait(timeout=timeout)

    def refit(self, records: Iterable[Any]) -> None:
        """Refit the ML head against an outcome stream, thread-safely.

        Acquires the head lock for the duration of the fit so a
        concurrent ``persist_head`` / ``advance`` cannot serialize a
        torn pipeline. When ``persist=True``, the new state is
        written to disk *after* the lock is released so the I/O does
        not block other threads.

        Prefer this over calling ``ml_head.fit(records)`` directly
        when the same switch is being persisted concurrently.
        """
        if self._ml_head is None:
            raise ValueError(f"switch {self.name!r} has no ml_head; cannot refit")
        with self._head_lock:
            self._ml_head.fit(records)
        # Save outside the head lock — the trained pipeline is the
        # current ``self._ml_head._pipeline``; the next ``state_bytes``
        # call will pickle it under the lock atomically.
        self._save_head_state()

    # --- Async API -----------------------------------------------------------
    #
    # Every sync method has an ``a``-prefixed async peer. The default
    # implementation wraps the sync call via ``asyncio.to_thread`` so
    # async callers (FastAPI, LangGraph, LlamaIndex, Starlette,
    # anything that runs on an event loop) can ``await`` classification
    # without surrendering a worker slot. No behavior change relative
    # to the sync API — same locks, same storage, same telemetry.
    #
    # Subclasses with a native-async storage backend (aiofiles,
    # aiosqlite) override ``aclassify`` / ``arecord_verdict`` to skip
    # the to_thread hop. The base implementation remains correct and
    # shippable for every storage we ship today.
    #
    # See docs/async.md for the interop contract (sync + async on the
    # same switch is supported; state is shared; locks protect both
    # entry points).

    async def aclassify(self, input: Any) -> ClassificationResult:
        """Async peer of :meth:`classify`.

        Runs the CPU-bound classify body in a worker thread. When
        ``config.verifier`` is set, the verdict-judgment runs on
        the event loop natively via the verifier's ``ajudge`` if
        available — so a cloud-language model verifier doesn't pin a thread
        for its full network latency. Falls back to a thread hop
        for sync-only verifiers.
        """
        import asyncio

        verifier = self.config.verifier
        if verifier is None:
            return await asyncio.to_thread(self.classify, input)

        # Run classify body in a worker (CPU-bound). The thread
        # call MUST NOT run the verifier — we want to do that on
        # the event loop natively.
        result = await asyncio.to_thread(self._classify_no_verifier, input)
        verified = await self._amaybe_run_verifier(input, result)
        if not verified and self.config.auto_record:
            await asyncio.to_thread(self._auto_log, input, result)
        try:
            self._telemetry.emit(
                "classify",
                {
                    "switch": self.name,
                    "phase": result.phase.value,
                    "source": result.source,
                    "confidence": result.confidence,
                    "verified": verified,
                },
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            pass
        return result

    def _classify_no_verifier(self, input: Any) -> ClassificationResult:
        """Sync classify body without the verifier hop.

        Used by :meth:`aclassify` so the verifier (often async-
        native via ``ajudge``) runs on the event loop instead of
        blocking the to_thread worker.
        """
        result = self._classify_impl(input)
        result._switch = self
        result._input = input
        return result

    async def _amaybe_run_verifier(self, input: Any, result: ClassificationResult) -> bool:
        """Async peer of :meth:`_maybe_run_verifier`.

        Uses ``verifier.ajudge`` when available; falls back to
        ``asyncio.to_thread(verifier.judge)`` for sync-only
        verifiers.
        """
        import asyncio

        verifier = self.config.verifier
        if verifier is None:
            return False
        if self.config.verifier_sample_rate < 1.0:
            import random

            if random.random() >= self.config.verifier_sample_rate:
                return False
        try:
            ajudge = getattr(verifier, "ajudge", None)
            if ajudge is not None:
                verdict = await ajudge(input, result.label)
            else:
                verdict = await asyncio.to_thread(
                    verifier.judge,
                    input,
                    result.label,
                )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            return False
        try:
            source_name = getattr(verifier, "source_name", "verifier")
            await self.arecord_verdict(
                input=input,
                label=result.label,
                outcome=verdict.value,
                source=source_name,
                confidence=result.confidence,
                _result_ctx=result,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            return False
        return True

    async def adispatch(self, input: Any) -> ClassificationResult:
        """Async peer of :meth:`dispatch`. Default: wraps sync in a thread.

        Note: the dispatched action itself is still sync. If the
        action is expensive and you're on an event loop, consider
        making the action itself a coroutine-launcher (schedule on
        the loop from inside) rather than blocking the thread.
        """
        import asyncio

        return await asyncio.to_thread(self.dispatch, input)

    async def arecord_verdict(
        self,
        *,
        input: Any,
        label: Any,
        outcome: str,
        source: str = "rule",
        confidence: float = 1.0,
        _result_ctx: ClassificationResult | None = None,
    ) -> None:
        """Async peer of :meth:`record_verdict`."""
        import asyncio
        import functools

        await asyncio.to_thread(
            functools.partial(
                self.record_verdict,
                input=input,
                label=label,
                outcome=outcome,
                source=source,
                confidence=confidence,
                _result_ctx=_result_ctx,
            ),
        )

    async def abulk_record_verdicts(self, verdicts: Iterable[BulkVerdict], /) -> BulkVerdictSummary:
        """Async peer of :meth:`bulk_record_verdicts`."""
        import asyncio

        # Materialize the iterable in the caller's thread; the sync
        # path expects something it can iterate deterministically,
        # and forcing it into the worker thread complicates that for
        # any iterable whose __iter__ touches caller-local state.
        batch = list(verdicts)
        return await asyncio.to_thread(self.bulk_record_verdicts, batch)

    async def abulk_record_verdicts_from_source(
        self, inputs: Iterable[Any], source: Any, /
    ) -> BulkVerdictSummary:
        """Async peer of :meth:`bulk_record_verdicts_from_source`.

        When the source exposes an ``ajudge(input, label)``
        coroutine, uses the async path (no per-judge thread hop);
        otherwise falls back to wrapping the sync pipeline in a
        thread. Subclasses of :class:`postrule.verdicts.VerdictSource`
        that want true-async behavior add ``ajudge``.
        """
        import asyncio

        ajudge = getattr(source, "ajudge", None)
        if ajudge is None:
            inputs = list(inputs)
            return await asyncio.to_thread(
                self.bulk_record_verdicts_from_source,
                inputs,
                source,
            )

        summary = BulkVerdictSummary()
        prior_auto_advance = self.config.auto_advance
        self.config.auto_advance = False
        try:
            for input in inputs:
                summary.total += 1
                try:
                    result = await self.aclassify(input)
                    verdict = await ajudge(input, result.label)
                    await self.arecord_verdict(
                        input=input,
                        label=result.label,
                        outcome=verdict.value,
                        source=source.source_name,
                        confidence=result.confidence,
                    )
                    summary.recorded += 1
                except (KeyboardInterrupt, SystemExit):
                    raise
                except BaseException:
                    summary.failed += 1
        finally:
            self.config.auto_advance = prior_auto_advance
        if prior_auto_advance and summary.recorded:
            with self._lock:
                self._records_since_advance_check = 0
            try:
                summary.auto_advance_decision = await asyncio.to_thread(
                    self.advance,
                    _auto=True,
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                pass
        return summary

    # --- Diagnostics -------------------------------------------------------

    @property
    def storage(self) -> Storage:
        """Public accessor — useful for tests and advanced wiring."""
        return self._storage
