# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""#148 / #145 G3 — LearnedSwitch.uninstall(verify=True).

Remove Postrule's learned footprint from a switch and *verify* the restore
took. Baseline mode reverts to the pre-install behaviour (phase RULE, the rule
decides, no learned state); snapshot mode restores a captured state. With
verify=True (default) it re-reads the state and raises if it didn't restore —
"uninstalling verifies the previous state has been set."
"""

from __future__ import annotations

import time

from postrule import LearnedSwitch, McNemarGate, Phase
from postrule.core import ClassificationRecord
from postrule.storage import FileStorage
from postrule.telemetry import NullEmitter


def _rec(rule_right: bool, model_right: bool):
    return ClassificationRecord(
        timestamp=time.time(),
        input={},
        label="a",
        outcome="correct",
        source="x",
        confidence=1.0,
        rule_output="a" if rule_right else "b",
        model_output="a" if model_right else "b",
        ml_output="b",
    )


def _seed(tmp_path, name, n=30):
    s = LearnedSwitch(
        rule=lambda _x: "a",
        name=name,
        starting_phase=Phase.MODEL_PRIMARY,
        storage=FileStorage(str(tmp_path)),
        persist=True,
        telemetry=NullEmitter(),
    )
    for i in range(n):
        s._storage.append_record(name, _rec(rule_right=(i < n - 5), model_right=(i < n // 4)))
    s._storage.put_state(name, "head", b"S")  # a learned head to clear
    return s


def test_uninstall_baseline_reverts_to_rule_and_clears_state(tmp_path):
    s = _seed(tmp_path, "uninst")
    assert s.phase() is Phase.MODEL_PRIMARY
    assert s._storage.get_state("uninst", "head") == b"S"

    assert s.uninstall() is True  # verify=True by default

    assert s.phase() is Phase.RULE  # rule decides again, as pre-install
    for key in ("head", "breaker", "signature", "ledger"):
        assert s._storage.get_state("uninst", key) is None


def test_uninstall_to_snapshot_restores_and_verifies(tmp_path):
    import postrule.defaults as D

    D.register_default_set(
        "uninst_strict",
        {
            "gate": lambda: McNemarGate(alpha=0.001, min_paired=20),
            "drift_gate": lambda: McNemarGate(),
        },
    )
    s = _seed(tmp_path, "uninst_snap", n=60)
    snap = s.snapshot()
    s.migrate_defaults(to_version="uninst_strict", accept_reset=True)
    assert s.phase() is Phase.RULE

    assert s.uninstall(to_snapshot=snap) is True
    assert s.phase() is Phase.MODEL_PRIMARY
    assert s._default_set_version == "v1"


def test_uninstall_cleared_state_persists_across_reopen(tmp_path):
    # The learned footprint stays gone: a fresh instance on the same storage
    # does not see the old head. (Phase on reopen follows the constructor once
    # the persisted signature is cleared, so we assert on the cleared state.)
    s = _seed(tmp_path, "uninst_reopen")
    s.uninstall()

    s2 = LearnedSwitch(
        rule=lambda _x: "a",
        name="uninst_reopen",
        starting_phase=Phase.RULE,
        storage=FileStorage(str(tmp_path)),
        persist=True,
        telemetry=NullEmitter(),
    )
    assert s2._storage.get_state("uninst_reopen", "head") is None
    assert s2.phase() is Phase.RULE


def test_uninstall_verify_false_skips_check(tmp_path):
    s = _seed(tmp_path, "uninst_noverify")
    assert s.uninstall(verify=False) is True
    assert s.phase() is Phase.RULE


def test_uninstall_proxied_on_decorated_fn():
    from postrule import ml_switch

    @ml_switch(name="uninst_proxied", starting_phase=Phase.RULE)
    def classify(_x):
        return "a"

    assert classify.uninstall() is True
