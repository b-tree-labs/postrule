# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""#148 / #145 G2 — per-switch snapshot + rollback.

Capture a switch's durable state (phase, pinned default-set, head, breaker,
signature, ledger) before an upgrade/migration, and restore it if the outcome
isn't wanted. The verdict log is append-only and intentionally NOT part of the
snapshot — rollback restores the decision state, the log stays as evidence.
"""

from __future__ import annotations

import time

from postrule import LearnedSwitch, McNemarGate, Phase, SwitchSnapshot
from postrule.core import ClassificationRecord
from postrule.storage import FileStorage
from postrule.telemetry import NullEmitter


def _rec(rule_right: bool, model_right: bool, ml_right: bool = False):
    return ClassificationRecord(
        timestamp=time.time(),
        input={},
        label="a",
        outcome="correct",
        source="x",
        confidence=1.0,
        rule_output="a" if rule_right else "b",
        model_output="a" if model_right else "b",
        ml_output="a" if ml_right else "b",
    )


def _seed_default(tmp_path, name, n, *, rule_wins=False):
    """A persisted, default-managed switch at MODEL_PRIMARY."""
    s = LearnedSwitch(
        rule=lambda _x: "a",
        name=name,
        starting_phase=Phase.MODEL_PRIMARY,
        storage=FileStorage(str(tmp_path)),
        persist=True,  # gate=None => default-managed
        telemetry=NullEmitter(),
    )
    for i in range(n):
        if rule_wins:
            s._storage.append_record(name, _rec(rule_right=(i < n - 5), model_right=(i < n // 4)))
    return s


def test_snapshot_is_read_only(tmp_path):
    s = _seed_default(tmp_path, "snap_ro", n=10, rule_wins=True)
    before = s.phase()
    snap = s.snapshot()
    assert isinstance(snap, SwitchSnapshot)
    assert snap.switch == "snap_ro"
    assert snap.phase is before
    assert snap.default_set_version == "v1"
    assert s.phase() is before  # snapshot never mutates


def test_rollback_restores_phase_and_version_after_accept_reset(tmp_path):
    import postrule.defaults as D

    D.register_default_set(
        "snap_strict",
        {
            "gate": lambda: McNemarGate(alpha=0.001, min_paired=20),
            "drift_gate": lambda: McNemarGate(),
        },
    )
    s = _seed_default(tmp_path, "snap_roll", n=60, rule_wins=True)
    assert s.phase() is Phase.MODEL_PRIMARY
    snap = s.snapshot()

    # Force-adopt the new gate, accepting the reset to RULE.
    assert s.migrate_defaults(to_version="snap_strict", accept_reset=True) is True
    assert s.phase() is Phase.RULE
    assert s._default_set_version == "snap_strict"

    # Roll back to the captured pre-migration state.
    s.rollback(snap)
    assert s.phase() is Phase.MODEL_PRIMARY
    assert s._default_set_version == "v1"


def test_rollback_persists_across_reopen(tmp_path):
    import postrule.defaults as D

    D.register_default_set(
        "snap_strict2",
        {
            "gate": lambda: McNemarGate(alpha=0.001, min_paired=20),
            "drift_gate": lambda: McNemarGate(),
        },
    )
    s = _seed_default(tmp_path, "snap_reopen", n=60, rule_wins=True)
    snap = s.snapshot()
    s.migrate_defaults(to_version="snap_strict2", accept_reset=True)
    s.rollback(snap)

    # A fresh instance on the same storage sees the rolled-back phase.
    s2 = LearnedSwitch(
        rule=lambda _x: "a",
        name="snap_reopen",
        starting_phase=Phase.MODEL_PRIMARY,
        storage=FileStorage(str(tmp_path)),
        persist=True,
        telemetry=NullEmitter(),
    )
    assert s2.phase() is Phase.MODEL_PRIMARY
