# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""#60 — judge / ML-strategy / statistical-gate swaps preserve graduation
integrity.

A switch's phase is *earned against a specific signal* (judge × ML-head
strategy × statistical gate). If any is swapped, the evidence that justified
the current phase may no longer hold. On construction (persist=True) the
switch detects a swap and re-justifies the current phase under the *new*
signal on a recency window, demoting toward the rule floor when it's no
longer earned — predictably, in every phase, with an audit ledger.

Reconcile is demote-only: it never auto-promotes, so a stale prior
graduation is re-earned via the normal gate, never blindly reinstated.
"""

from __future__ import annotations

import json
import time

from postrule import LearnedSwitch, McNemarGate, Phase
from postrule.core import ClassificationRecord
from postrule.signals import SignalSignature, signal_signature
from postrule.storage import FileStorage


# ---------------------------------------------------------------------------
# SignalSignature unit
# ---------------------------------------------------------------------------
class TestSignalSignature:
    def test_core_identity_excludes_head_version(self) -> None:
        a = SignalSignature("judge:x", ("McNemarGate", 0.01, 200), None, "sklearn-100")
        b = SignalSignature("judge:x", ("McNemarGate", 0.01, 200), None, "sklearn-999")
        # Same judge/gate/strategy, different retrain → NOT a swap.
        assert a.core_identity() == b.core_identity()
        assert a.changed_components(b) == []

    def test_changed_components_detects_each(self) -> None:
        base = SignalSignature("judge:a", ("McNemarGate", 0.01, 200), None, None)
        assert base.changed_components(
            SignalSignature("judge:b", ("McNemarGate", 0.01, 200), None, None)
        ) == ["judge"]
        assert base.changed_components(
            SignalSignature("judge:a", ("McNemarGate", 0.05, 200), None, None)
        ) == ["gate"]
        assert base.changed_components(
            SignalSignature("judge:a", ("McNemarGate", 0.01, 200), ("S", ()), None)
        ) == ["strategy"]

    def test_audit_round_trip(self) -> None:
        s = SignalSignature("judge:x", ("McNemarGate", 0.01, 200), ("Strat", (("t", 5),)), "v1")
        back = SignalSignature.from_audit(json.loads(json.dumps(s.to_audit())))
        assert back == s
        assert back.digest() == s.digest()

    def test_signature_from_live_switch(self, tmp_path) -> None:
        s = LearnedSwitch(
            rule=lambda _x: "a",
            name="sig",
            starting_phase=Phase.RULE,
            gate=McNemarGate(alpha=0.02, min_paired=50),
        )
        sig = signal_signature(s)
        assert sig.gate_id == ("McNemarGate", 0.02, 50)
        assert sig.judge_id is None  # no verifier

    def test_identity_components(self) -> None:
        from postrule.ml_strategy import CardinalityMLHeadStrategy
        from postrule.signals import (
            _gate_identity,
            _head_version,
            _judge_identity,
            _strategy_identity,
        )

        # gate None
        assert _gate_identity(None) == ("None",)
        # judge with source_name vs class-name fallback
        assert _judge_identity(type("J", (), {"source_name": "judge:x"})()) == "judge:x"
        assert _judge_identity(type("Bare", (), {})()) == "Bare"
        assert _judge_identity(None) is None
        # strategy identity captures class name + scalar threshold attrs
        sid = _strategy_identity(CardinalityMLHeadStrategy())
        assert sid is not None and sid[0] == "CardinalityMLHeadStrategy"
        assert _strategy_identity(None) is None
        # head version
        assert _head_version(type("H", (), {"model_version": lambda self: "v9"})()) == "v9"
        assert _head_version(None) is None
        assert _head_version(type("NoVer", (), {})()) is None

    def test_digest_stable_and_distinct(self) -> None:
        a = SignalSignature("j", ("McNemarGate", 0.01, 200), None, "v1")
        b = SignalSignature("j", ("McNemarGate", 0.01, 200), None, "v2")
        c = SignalSignature("j", ("McNemarGate", 0.05, 200), None, "v1")
        assert a.digest() == b.digest()  # head version excluded from identity
        assert a.digest() != c.digest()  # gate change → different digest


# ---------------------------------------------------------------------------
# Reconcile matrix harness
# ---------------------------------------------------------------------------
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


def _build(tmp_path, name, phase, *, gate, n=0, rule_wins=False, target_wins=False, **cfg):
    """A persisted switch at `phase`. A fresh FileStorage instance on the same
    dir simulates a new process/replica (so reconcile reads persisted state)."""
    s = LearnedSwitch(
        rule=lambda _x: "a",
        name=name,
        starting_phase=phase,
        gate=gate,
        storage=FileStorage(str(tmp_path)),
        persist=True,
        **cfg,
    )
    for i in range(n):
        if target_wins:  # higher tier beats rule (justified)
            s._storage.append_record(
                name, _rec(rule_right=(i < n // 4), model_right=(i < n - 5), ml_right=(i < n - 5))
            )
        elif rule_wins:  # rule beats higher tier (NOT justified)
            s._storage.append_record(
                name, _rec(rule_right=(i < n - 5), model_right=(i < n // 4), ml_right=(i < n // 4))
            )
    return s


def _events(switch):
    led = (switch._storage.get_state(switch.name, "ledger") or b"").decode()
    return [json.loads(line)["event"] for line in led.strip().splitlines() if line.strip()]


def _reopen(tmp_path, name, phase, *, gate, **cfg):
    return LearnedSwitch(
        rule=lambda _x: "a",
        name=name,
        starting_phase=phase,
        gate=gate,
        storage=FileStorage(str(tmp_path)),
        persist=True,
        **cfg,
    )


class TestReconcileCore:
    def test_fresh_switch_adopts_never_demotes(self, tmp_path) -> None:
        s = _build(
            tmp_path,
            "fresh",
            Phase.MODEL_PRIMARY,
            gate=McNemarGate(min_paired=20),
            n=40,
            rule_wins=True,
        )
        assert s.phase() is Phase.MODEL_PRIMARY  # no demote on first run
        assert _events(s) == ["adopt"]

    def test_no_swap_is_noop(self, tmp_path) -> None:
        _build(
            tmp_path,
            "same",
            Phase.MODEL_PRIMARY,
            gate=McNemarGate(alpha=0.01, min_paired=20),
            n=40,
            rule_wins=True,
        )
        s2 = _reopen(
            tmp_path, "same", Phase.MODEL_PRIMARY, gate=McNemarGate(alpha=0.01, min_paired=20)
        )
        assert s2.phase() is Phase.MODEL_PRIMARY
        assert "swap" not in _events(s2)

    def test_swap_unjustified_demotes_with_audit(self, tmp_path) -> None:
        _build(
            tmp_path,
            "swp",
            Phase.MODEL_PRIMARY,
            gate=McNemarGate(alpha=0.01, min_paired=20),
            n=60,
            rule_wins=True,
        )
        s2 = _reopen(
            tmp_path, "swp", Phase.MODEL_PRIMARY, gate=McNemarGate(alpha=0.05, min_paired=20)
        )
        assert s2.phase() is Phase.RULE  # rule wins → cascade to floor
        ev = _events(s2)
        assert "swap" in ev and "demote" in ev

    def test_swap_still_justified_holds_phase(self, tmp_path) -> None:
        _build(
            tmp_path,
            "ok",
            Phase.MODEL_PRIMARY,
            gate=McNemarGate(alpha=0.01, min_paired=20),
            n=60,
            target_wins=True,
        )
        s2 = _reopen(
            tmp_path, "ok", Phase.MODEL_PRIMARY, gate=McNemarGate(alpha=0.05, min_paired=20)
        )
        assert s2.phase() is Phase.MODEL_PRIMARY  # still earned under new gate
        ev = _events(s2)
        assert "swap" in ev and "demote" not in ev

    def test_swap_insufficient_recent_evidence_holds(self, tmp_path) -> None:
        # Few records → below min_paired → don't punish; hold the phase.
        _build(
            tmp_path,
            "few",
            Phase.MODEL_PRIMARY,
            gate=McNemarGate(alpha=0.01, min_paired=200),
            n=10,
            rule_wins=True,
        )
        s2 = _reopen(
            tmp_path, "few", Phase.MODEL_PRIMARY, gate=McNemarGate(alpha=0.05, min_paired=200)
        )
        assert s2.phase() is Phase.MODEL_PRIMARY
        assert "demote" not in _events(s2)

    def test_flag_mode_audits_but_holds(self, tmp_path) -> None:
        _build(
            tmp_path,
            "flg",
            Phase.MODEL_PRIMARY,
            gate=McNemarGate(alpha=0.01, min_paired=20),
            n=60,
            rule_wins=True,
        )
        s2 = _reopen(
            tmp_path,
            "flg",
            Phase.MODEL_PRIMARY,
            gate=McNemarGate(alpha=0.05, min_paired=20),
            signal_swap_action="flag",
        )
        assert s2.phase() is Phase.MODEL_PRIMARY  # flagged, not moved
        assert "swap" in _events(s2) and "demote" not in _events(s2)

    def test_rule_phase_swap_is_noop(self, tmp_path) -> None:
        _build(tmp_path, "r", Phase.RULE, gate=McNemarGate(min_paired=20), n=40, rule_wins=True)
        s2 = _reopen(tmp_path, "r", Phase.RULE, gate=McNemarGate(alpha=0.05, min_paired=20))
        assert s2.phase() is Phase.RULE
        assert "demote" not in _events(s2)

    def test_reconcile_idempotent(self, tmp_path) -> None:
        _build(
            tmp_path,
            "idem",
            Phase.MODEL_PRIMARY,
            gate=McNemarGate(alpha=0.01, min_paired=20),
            n=60,
            rule_wins=True,
        )
        s2 = _reopen(
            tmp_path, "idem", Phase.MODEL_PRIMARY, gate=McNemarGate(alpha=0.05, min_paired=20)
        )
        ev_after_first = _events(s2)
        s2.reconcile_signals()  # second call, same signal → no new events
        assert _events(s2) == ev_after_first

    def test_disabled_skips_reconcile(self, tmp_path) -> None:
        _build(
            tmp_path,
            "off",
            Phase.MODEL_PRIMARY,
            gate=McNemarGate(alpha=0.01, min_paired=20),
            n=60,
            rule_wins=True,
        )
        s2 = _reopen(
            tmp_path,
            "off",
            Phase.MODEL_PRIMARY,
            gate=McNemarGate(alpha=0.05, min_paired=20),
            reconcile_signals=False,
        )
        assert s2.phase() is Phase.MODEL_PRIMARY
        assert _events(s2) == ["adopt"]  # only the first switch's adopt; no swap processed
