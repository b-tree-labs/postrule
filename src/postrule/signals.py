# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Signal signatures for graduation integrity (#60).

A switch's phase and any trained ML head were *earned against a specific
truth signal*: a specific judge/verifier, a specific :class:`MLHeadStrategy`,
and a specific statistical gate. If any of those is swapped, the evidence
that justified the current phase may no longer hold. A :class:`SignalSignature`
captures the identity of those three signals so a swap is *recognizable*
rather than silent — the input to ``LearnedSwitch.reconcile_signals``.

Identities are built defensively (``getattr``) since the pluggables don't
implement ``__eq__``/``__hash__``. ``core_identity`` deliberately excludes
the ML-head *version* (which changes on every retrain) so routine retraining
is never misread as a swap; the version rides along for audit only.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

__all__ = ["SignalSignature", "signal_signature"]


def _gate_identity(gate: Any) -> tuple:
    """Stable identity tuple for a statistical gate (test-agnostic).

    ``(class_name, alpha, min_paired)`` — covers the built-in gates and any
    custom gate exposing ``alpha``/``min_paired`` (or the private spellings).
    Frames on the *gate* abstraction, not a specific test.
    """
    if gate is None:
        return ("None",)
    name = type(gate).__name__
    alpha = getattr(gate, "alpha", getattr(gate, "_alpha", None))
    min_paired = getattr(gate, "min_paired", getattr(gate, "_min_paired", None))
    return (name, alpha, min_paired)


def _strategy_identity(strategy: Any) -> tuple | None:
    """Stable identity for an ML-head strategy: class name + its threshold
    attributes (sorted, JSON-coercible scalars only). ``None`` when no
    strategy is configured.
    """
    if strategy is None:
        return None
    name = type(strategy).__name__
    attrs: list[tuple[str, Any]] = []
    raw = getattr(strategy, "__dict__", None) or {}
    for k in sorted(raw):
        v = raw[k]
        if isinstance(v, (int, float, str, bool)) or v is None:
            attrs.append((k, v))
    return (name, tuple(attrs))


def _judge_identity(verifier: Any) -> str | None:
    """The judge/verifier's stable name, or ``None`` when none is configured."""
    if verifier is None:
        return None
    return getattr(verifier, "source_name", type(verifier).__name__)


def _labels_identity(switch: Any) -> tuple | None:
    """Stable identity for the switch's label set (the classification space).

    ``tuple(sorted((name, on-repr)))`` over the configured labels, or ``None``
    when no labels are declared. A change here — an added/removed/renamed label
    or a changed conditional expression — alters the space a trained head was
    fit to and the space accuracy was measured in, so it must register as a
    swap (and quarantine the head; see ``LearnedSwitch.reconcile_signals``).
    """
    raw = getattr(switch, "_labels_raw", None)
    if not raw:
        return None
    items: list[tuple[str, str | None]] = []
    for lbl in raw:
        name = getattr(lbl, "name", lbl)
        on = getattr(lbl, "on", None)
        items.append((str(name), repr(on) if on is not None else None))
    return tuple(sorted(items))


def _rule_drift_digest(rule: Any) -> str | None:
    """Advisory-only digest of the rule's compiled bytecode.

    Catches *behavioral* edits to the rule body (not comments/whitespace) while
    staying out of ``core_identity`` — a refactor or Python-version bump must
    NOT auto-demote a hard-earned head (that's the pin-on-upgrade principle).
    The demote trigger for a rule change is the explicit ``rule_version``; this
    digest only powers a surfaced "rule drift" advisory. ``None`` when the rule
    exposes no ``__code__`` (C callable, partial, etc.).
    """
    code = getattr(rule, "__code__", None)
    if code is None:
        fn = getattr(rule, "__func__", None)  # bound method
        code = getattr(fn, "__code__", None)
    co_code = getattr(code, "co_code", None)
    if co_code is None:
        return None
    # Include co_consts/co_names so a constant-only edit (a tweaked threshold or
    # returned label) is caught — same opcodes, different embedded values.
    parts = [bytes(co_code)]
    for attr in ("co_consts", "co_names"):
        val = getattr(code, attr, None)
        if val is not None:
            parts.append(repr(val).encode("utf-8", "replace"))
    return hashlib.blake2s(b"|".join(parts), digest_size=8).hexdigest()


def _head_version(head: Any) -> str | None:
    if head is None:
        return None
    fn = getattr(head, "model_version", None)
    if not callable(fn):
        return None
    try:
        v = fn()
    except Exception:  # noqa: BLE001 — version is best-effort audit metadata
        return None
    return str(v) if v is not None else None


@dataclass(frozen=True)
class SignalSignature:
    """Identity of the three signals that justify a switch's graduation."""

    judge_id: str | None
    gate_id: tuple
    strategy_id: tuple | None
    ml_head_version: str | None
    # Added after ml_head_version so positional construction stays compatible.
    labels_id: tuple | None = None
    rule_version_id: str | None = None
    # Advisory-only: NOT part of core_identity (never auto-demotes).
    rule_drift_digest: str | None = None

    def core_identity(self) -> tuple:
        """The swap-detection key. Excludes ``ml_head_version`` so a routine
        retrain (which bumps the version) is not misread as a signal swap, and
        excludes ``rule_drift_digest`` so a rule refactor doesn't auto-demote
        (only an explicit ``rule_version`` bump does)."""
        return (
            self.judge_id,
            self.gate_id,
            self.strategy_id,
            self.labels_id,
            self.rule_version_id,
        )

    def changed_components(self, other: SignalSignature) -> list[str]:
        """Which of judge / gate / strategy / labels / rule differ from ``other``."""
        changed = []
        if self.judge_id != other.judge_id:
            changed.append("judge")
        if self.gate_id != other.gate_id:
            changed.append("gate")
        if self.strategy_id != other.strategy_id:
            changed.append("strategy")
        if self.labels_id != other.labels_id:
            changed.append("labels")
        if self.rule_version_id != other.rule_version_id:
            changed.append("rule")
        return changed

    def to_audit(self) -> dict[str, Any]:
        """JSON-serializable form for the persisted signature + ledger."""
        return {
            "judge_id": self.judge_id,
            "gate_id": list(self.gate_id),
            "strategy_id": _jsonable(self.strategy_id),
            "ml_head_version": self.ml_head_version,
            "labels_id": _jsonable(self.labels_id),
            "rule_version_id": self.rule_version_id,
            "rule_drift_digest": self.rule_drift_digest,
        }

    @classmethod
    def from_audit(cls, d: dict[str, Any]) -> SignalSignature:
        sid = d.get("strategy_id")
        lid = d.get("labels_id")
        return cls(
            judge_id=d.get("judge_id"),
            gate_id=tuple(d.get("gate_id") or ()),
            strategy_id=_tuplify(sid) if sid is not None else None,
            ml_head_version=d.get("ml_head_version"),
            labels_id=_tuplify(lid) if lid is not None else None,
            rule_version_id=d.get("rule_version_id"),
            rule_drift_digest=d.get("rule_drift_digest"),
        )

    def digest(self) -> str:
        """Short stable hash of the core identity (for compact comparison)."""
        canonical = json.dumps(
            [
                self.judge_id,
                list(self.gate_id),
                _jsonable(self.strategy_id),
                _jsonable(self.labels_id),
                self.rule_version_id,
            ],
            sort_keys=True,
            default=str,
        )
        return hashlib.blake2s(canonical.encode("utf-8"), digest_size=8).hexdigest()


def _jsonable(v: Any) -> Any:
    """Recursively turn tuples into lists so round-tripping through JSON is
    stable (json emits lists; we re-tuplify on load)."""
    if isinstance(v, tuple):
        return [_jsonable(x) for x in v]
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return v


def _tuplify(v: Any) -> Any:
    if isinstance(v, list):
        return tuple(_tuplify(x) for x in v)
    return v


def signal_signature(switch: Any) -> SignalSignature:
    """Build the current :class:`SignalSignature` from a live switch.

    Reads the configured verifier + gate (``switch.config``) and the head
    strategy + ML head (instance attrs). Pure and defensive.
    """
    config = getattr(switch, "config", None)
    verifier = getattr(config, "verifier", None) if config is not None else None
    gate = getattr(config, "gate", None) if config is not None else None
    strategy = getattr(switch, "_head_strategy", None)
    head = getattr(switch, "_ml_head", None)
    # A library-default gate identifies by its pinned default-set *version*,
    # not the concrete params — so a code-default change is recognized as
    # default-drift (pinned), not an operator swap (#60 PR3).
    if getattr(switch, "_gate_is_default", False):
        gate_id: tuple = ("__default__", getattr(switch, "_default_set_version", None))
    else:
        gate_id = _gate_identity(gate)
    rule = getattr(switch, "_rule", None)
    return SignalSignature(
        judge_id=_judge_identity(verifier),
        gate_id=gate_id,
        strategy_id=_strategy_identity(strategy),
        ml_head_version=_head_version(head),
        labels_id=_labels_identity(switch),
        rule_version_id=getattr(config, "rule_version", None) if config is not None else None,
        rule_drift_digest=_rule_drift_digest(rule),
    )
