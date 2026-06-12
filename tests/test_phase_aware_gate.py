# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Tests for PhaseAwareGate + the v2 per-phase default-set.

The whitepaper's graduated-autonomy uses a *different* notion of "good enough to
advance" per transition: strict superiority to promote UP toward the costly LLM
(or to drop the rule fallback at ML_PRIMARY), but non-inferiority — graduate on a
tie — to move the cheap trained head OVER the LLM. PhaseAwareGate dispatches the
right sub-gate per (current, target) transition; v2 wires it as a default-set.
"""

from __future__ import annotations

import pytest

from postrule.core import Phase
from postrule.gates import (
    CostToleranceGate,
    McNemarGate,
    PhaseAwareGate,
)


class _Yes:
    def evaluate(self, records, current, target, /):  # noqa: ARG002
        from postrule.gates import GateDecision

        return GateDecision(target_better=True, rationale="yes")


class _No:
    def evaluate(self, records, current, target, /):  # noqa: ARG002
        from postrule.gates import GateDecision

        return GateDecision(target_better=False, rationale="no")


def test_dispatches_by_transition():
    gate = PhaseAwareGate(
        {(Phase.ML_SHADOW, Phase.ML_WITH_FALLBACK): _Yes()},
        default=_No(),
    )
    # Mapped transition uses the specific gate.
    assert gate.evaluate([], Phase.ML_SHADOW, Phase.ML_WITH_FALLBACK).target_better
    # Unmapped transition falls back to the default.
    assert not gate.evaluate([], Phase.RULE, Phase.MODEL_SHADOW).target_better


def test_requires_a_default():
    with pytest.raises(ValueError, match="default"):
        PhaseAwareGate({}, default=None)  # type: ignore[arg-type]


def test_exposes_mapping_and_default():
    d = _No()
    g = PhaseAwareGate({(Phase.ML_SHADOW, Phase.ML_WITH_FALLBACK): _Yes()}, default=d)
    assert g.default is d
    assert (Phase.ML_SHADOW, Phase.ML_WITH_FALLBACK) in g.transition_gates


def test_rationale_names_the_chosen_gate():
    g = PhaseAwareGate({(Phase.ML_SHADOW, Phase.ML_WITH_FALLBACK): _Yes()}, default=_No())
    r = g.evaluate([], Phase.ML_SHADOW, Phase.ML_WITH_FALLBACK)
    assert "ML_SHADOW" in r.rationale and "ML_WITH_FALLBACK" in r.rationale


# --- v2 default-set ---------------------------------------------------------


def test_v2_registered_and_resolvable():
    from postrule.defaults import known_default_set_versions, resolve_gate

    assert "v2" in known_default_set_versions()
    gate = resolve_gate("v2")
    assert isinstance(gate, PhaseAwareGate)


def test_v2_graduates_off_llm_on_non_inferiority():
    # The ML_SHADOW -> ML_WITH_FALLBACK transition (cheap head replaces the LLM)
    # must use a non-inferiority gate so it graduates on a tie.
    from postrule.defaults import resolve_gate

    gate = resolve_gate("v2")
    off_llm = gate.transition_gates[(Phase.ML_SHADOW, Phase.ML_WITH_FALLBACK)]
    assert isinstance(off_llm, CostToleranceGate)


def test_v2_promotes_up_with_strict_superiority():
    # Promoting toward the LLM, and dropping the rule fallback at ML_PRIMARY,
    # must require strict superiority (McNemar) — never advance on a mere tie.
    from postrule.defaults import resolve_gate

    gate = resolve_gate("v2")
    assert isinstance(gate.default, McNemarGate)
    # Removing the rule fallback (-> ML_PRIMARY) is strict, not non-inferiority.
    up = gate.transition_gates.get((Phase.ML_WITH_FALLBACK, Phase.ML_PRIMARY), gate.default)
    assert isinstance(up, McNemarGate)


def test_v2_is_not_the_global_default_yet():
    # v2 is opt-in (migrate_defaults) until validated; new switches still pin v1.
    from postrule.defaults import CURRENT_DEFAULT_SET_VERSION

    assert CURRENT_DEFAULT_SET_VERSION == "v1"


def test_default_switch_still_uses_v1_mcnemar():
    from postrule.core import LearnedSwitch

    sw = LearnedSwitch(name="v1-default", rule=lambda x: "a")
    assert isinstance(sw.config.gate, McNemarGate)
