# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Versioned default-sets for upgrade safety (#60 PR 3).

A switch that graduated using *library-default* signals (e.g. ``gate=None``)
pinned that behavior to a specific **default-set version**. A later
``pip install -U`` that ships a new default must NOT silently change a
graduated switch — so defaults are versioned data, the switch records the
version it graduated under, and ``reconcile_signals`` keeps resolving the
*pinned* version (not the newest) until the operator explicitly migrates.

Sourcing is "embedded baseline + cloud override": the embedded registry below
is the always-available floor (and the cross-language reference); a cloud /
operator-supplied set can be registered to offer a newer version a switch may
migrate to via :meth:`LearnedSwitch.migrate_defaults`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

__all__ = [
    "CURRENT_DEFAULT_SET_VERSION",
    "resolve_gate",
    "register_default_set",
    "known_default_set_versions",
]

# The newest embedded default-set. New switches pin this; graduated switches
# keep whatever they pinned. Bump when shipping a new default (and add the new
# version below WITHOUT removing old ones — pinned switches still resolve them).
CURRENT_DEFAULT_SET_VERSION = "v1"


def _gate_v1() -> Any:
    from postrule.gates import McNemarGate

    return McNemarGate()  # alpha=0.01, min_paired=200


def _gate_v2() -> Any:
    """Per-phase graduation: strict superiority to promote UP toward the costly
    LLM (and to drop the rule fallback at ML_PRIMARY), non-inferiority to
    graduate the cheap trained head OVER the LLM (ML_SHADOW -> ML_WITH_FALLBACK).

    This encodes the whitepaper's "what counts as inferior" per phase: adopting
    or keeping the LLM demands a reliable win; shedding it for a cheaper tier
    that ties is the right move. Opt-in via ``migrate_defaults`` until validated.
    """
    from postrule.core import Phase
    from postrule.gates import CostToleranceGate, McNemarGate, PhaseAwareGate

    return PhaseAwareGate(
        {
            # Graduate the cheap head over the LLM on a statistical tie.
            (Phase.ML_SHADOW, Phase.ML_WITH_FALLBACK): CostToleranceGate(margin=0.02),
        },
        # Everything else — promotions toward the LLM, and dropping the rule
        # fallback at ML_PRIMARY — requires strict superiority.
        default=McNemarGate(),
    )


# version -> {signal: factory}. Old versions stay forever so a switch pinned to
# an older version can always reconstruct the exact default it graduated under.
_DEFAULT_SETS: dict[str, dict[str, Callable[[], Any]]] = {
    "v1": {"gate": _gate_v1, "drift_gate": _gate_v1},
    # v2 is registered (adoptable via migrate_defaults) but NOT yet the current
    # version — flipping the global default is a behavior change to validate
    # (e.g. on the SoilMetrix deployment) before new switches pin it.
    "v2": {"gate": _gate_v2, "drift_gate": _gate_v1},
}


def register_default_set(version: str, factories: dict[str, Callable[[], Any]]) -> None:
    """Register a default-set version (the cloud-override seam).

    Cloud / operator code can publish a newer versioned set; switches may
    then ``migrate_defaults(to_version=...)`` onto it. Embedded versions are
    never overwritten (the baseline floor stays authoritative).
    """
    if version in _DEFAULT_SETS:
        return
    _DEFAULT_SETS[version] = dict(factories)


def known_default_set_versions() -> list[str]:
    return sorted(_DEFAULT_SETS)


def resolve_gate(version: str | None, key: str = "gate") -> Any | None:
    """Resolve the library-default gate for a default-set version.

    Falls back to the current version when ``version`` is unknown (e.g. a
    switch pinned a cloud version this SDK build doesn't have) — fail toward a
    working gate rather than crash; reconcile re-justifies regardless.
    """
    table = _DEFAULT_SETS.get(version or "") or _DEFAULT_SETS[CURRENT_DEFAULT_SET_VERSION]
    factory = table.get(key)
    return factory() if factory is not None else None
