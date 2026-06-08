# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""#145 PR1 — process-wide ``migrate_all`` + ``accept_reset``.

``migrate_all`` adopts a new default-set gate across every live switch in one
call — the operator entry point after ``pip install -U``, since #60 default
pinning keeps switches frozen until explicitly asked to move.

``accept_reset=True`` forces adoption even when the old evidence no longer
re-justifies the phase: the switch resets to RULE and re-graduates under the
new gate from its preserved log (never silently, never losing the log).
"""

from __future__ import annotations

import gc
import json
import time

import pytest

from postrule import LearnedSwitch, McNemarGate, Phase, migrate_all
from postrule.core import ClassificationRecord
from postrule.storage import FileStorage


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


def _seed(
    tmp_path,
    name,
    n,
    *,
    rule_wins=False,
    target_wins=False,
    gate=None,
    phase=Phase.MODEL_PRIMARY,
):
    """A persisted switch. ``gate=None`` => default-managed (migratable)."""
    s = LearnedSwitch(
        rule=lambda _x: "a",
        name=name,
        starting_phase=phase,
        gate=gate,
        storage=FileStorage(str(tmp_path)),
        persist=True,
    )
    for i in range(n):
        if target_wins:  # higher tier beats the rule => phase justified
            s._storage.append_record(name, _rec(rule_right=(i < n // 4), model_right=(i < n - 5)))
        elif rule_wins:  # rule beats higher tier => NOT justified
            s._storage.append_record(name, _rec(rule_right=(i < n - 5), model_right=(i < n // 4)))
    return s


def _events(switch):
    led = (switch._storage.get_state(switch.name, "ledger") or b"").decode()
    return [json.loads(line)["event"] for line in led.strip().splitlines() if line.strip()]


@pytest.fixture(autouse=True)
def _drop_stale_switches():
    # Drop weakrefs to switches from earlier tests so migrate_all only acts on
    # this test's live switches; assertions still filter by name for safety.
    gc.collect()
    yield


def test_migrate_all_adopts_when_justified(tmp_path):
    import postrule.defaults as D

    D.register_default_set(
        "mv_ok",
        {
            "gate": lambda: McNemarGate(alpha=0.05, min_paired=20),
            "drift_gate": lambda: McNemarGate(),
        },
    )
    s = _seed(tmp_path, "mall_ok", n=60, target_wins=True)
    assert s._default_set_version == "v1"

    out = {o.switch: o for o in migrate_all(to_version="mv_ok")}
    assert out["mall_ok"].result == "migrated"
    assert out["mall_ok"].phase_after is Phase.MODEL_PRIMARY
    assert s._default_set_version == "mv_ok"
    assert "migrate" in _events(s)


def test_migrate_all_holds_when_unjustified(tmp_path):
    import postrule.defaults as D

    D.register_default_set(
        "mv_strict",
        {
            "gate": lambda: McNemarGate(alpha=0.001, min_paired=20),
            "drift_gate": lambda: McNemarGate(),
        },
    )
    s = _seed(tmp_path, "mall_hold", n=60, rule_wins=True)

    out = {o.switch: o for o in migrate_all(to_version="mv_strict")}
    assert out["mall_hold"].result == "held"
    assert s.phase() is Phase.MODEL_PRIMARY  # phase unchanged
    assert s._default_set_version == "v1"  # pinned version kept
    assert "migrate_rejected" in _events(s)


def test_accept_reset_adopts_and_resets_to_rule(tmp_path):
    import postrule.defaults as D

    D.register_default_set(
        "mv_reset",
        {
            "gate": lambda: McNemarGate(alpha=0.001, min_paired=20),
            "drift_gate": lambda: McNemarGate(),
        },
    )
    s = _seed(tmp_path, "mall_reset", n=60, rule_wins=True)
    assert s.phase() is Phase.MODEL_PRIMARY

    out = {o.switch: o for o in migrate_all(to_version="mv_reset", accept_reset=True)}
    assert out["mall_reset"].result == "reset"
    assert out["mall_reset"].phase_before is Phase.MODEL_PRIMARY
    assert s.phase() is Phase.RULE  # reset to step 1 to re-graduate
    assert s._default_set_version == "mv_reset"  # adopted despite the reset
    assert "migrate_reset" in _events(s)


def test_migrate_all_noop_for_operator_gate(tmp_path):
    # Operator-supplied gate is not default-managed => migrate_all leaves it.
    s = _seed(
        tmp_path,
        "mall_op",
        n=10,
        gate=McNemarGate(alpha=0.01, min_paired=20),
    )
    out = {o.switch: o for o in migrate_all(to_version="anything")}
    assert out["mall_op"].result == "noop"
    assert s.phase() is Phase.MODEL_PRIMARY
