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


class _Judge:
    def __init__(self, name):
        self.source_name = name

    def judge(self, _inp, _label):  # pragma: no cover — not exercised in reconcile
        return None


class _StubHead:
    def fit(self, _recs):
        pass

    def predict(self, _inp, _labels):  # pragma: no cover
        from postrule.ml import MLPrediction

        return MLPrediction(label="a", confidence=0.9)

    def model_version(self):
        return "stub-1"

    def state_bytes(self):
        return b"S"

    def load_state(self, _b):
        pass


class TestStaleMLQuarantine:
    def _seed(self, tmp_path, name, judge, gate, n=60):
        s = LearnedSwitch(
            rule=lambda _x: "a",
            name=name,
            starting_phase=Phase.MODEL_PRIMARY,
            gate=gate,
            verifier=judge,
            ml_head=_StubHead(),
            storage=FileStorage(str(tmp_path)),
            persist=True,
        )
        for i in range(n):  # target wins → phase stays justified (isolate quarantine from demote)
            s._storage.append_record(name, _rec(rule_right=(i < n // 4), model_right=(i < n - 5)))
        s._storage.put_state(name, "head", b"S")  # ensure a persisted head exists
        return s

    def _reopen(self, tmp_path, name, judge, gate):
        return LearnedSwitch(
            rule=lambda _x: "a",
            name=name,
            starting_phase=Phase.MODEL_PRIMARY,
            gate=gate,
            verifier=judge,
            ml_head=_StubHead(),
            storage=FileStorage(str(tmp_path)),
            persist=True,
        )

    def test_judge_swap_quarantines_head(self, tmp_path) -> None:
        g = McNemarGate(alpha=0.01, min_paired=20)
        self._seed(tmp_path, "q1", _Judge("judge:a"), g)
        s2 = self._reopen(tmp_path, "q1", _Judge("judge:b"), McNemarGate(alpha=0.01, min_paired=20))
        assert s2._storage.get_state("q1", "head") is None  # stale weights dropped
        assert "quarantine" in _events(s2)

    def test_gate_only_swap_does_not_quarantine(self, tmp_path) -> None:
        self._seed(tmp_path, "q2", _Judge("judge:a"), McNemarGate(alpha=0.01, min_paired=20))
        s2 = self._reopen(tmp_path, "q2", _Judge("judge:a"), McNemarGate(alpha=0.05, min_paired=20))
        # Gate-only swap: the head was not trained by the gate → keep it.
        assert s2._storage.get_state("q2", "head") == b"S"
        assert "quarantine" not in _events(s2)


class TestDefaultSetUpgrade:
    """#60 PR3 — a graduated switch on gate=None pins its default-set version;
    an SDK upgrade that changes the default must not silently re-grade it.
    migrate_defaults() is the opt-in, evidence-gated path forward."""

    def _seed_default(self, tmp_path, name, n, *, rule_wins=False, target_wins=False):
        s = LearnedSwitch(
            rule=lambda _x: "a",
            name=name,
            starting_phase=Phase.MODEL_PRIMARY,
            storage=FileStorage(str(tmp_path)),
            persist=True,  # gate=None → default
        )
        for i in range(n):
            if target_wins:
                s._storage.append_record(
                    name, _rec(rule_right=(i < n // 4), model_right=(i < n - 5))
                )
            elif rule_wins:
                s._storage.append_record(
                    name, _rec(rule_right=(i < n - 5), model_right=(i < n // 4))
                )
        return s

    def test_upgrade_default_change_pins_not_demotes(self, tmp_path, monkeypatch) -> None:
        import postrule.defaults as D

        s1 = self._seed_default(tmp_path, "pin", n=60, rule_wins=True)
        assert s1._default_set_version == "v1"
        assert s1._gate_is_default is True

        # Simulate `pip install -U`: a NEW, stricter default-set v2 ships.
        D.register_default_set(
            "v2", {"gate": lambda: McNemarGate(alpha=0.5), "drift_gate": lambda: McNemarGate()}
        )
        monkeypatch.setattr(D, "CURRENT_DEFAULT_SET_VERSION", "v2")

        s2 = LearnedSwitch(
            rule=lambda _x: "a",
            name="pin",
            starting_phase=Phase.MODEL_PRIMARY,
            storage=FileStorage(str(tmp_path)),
            persist=True,
        )
        # Pinned to v1 — NOT re-graded despite rule-wins evidence + new default.
        assert s2.phase() is Phase.MODEL_PRIMARY
        assert s2._default_set_version == "v1"
        ev = _events(s2)
        assert "pin" in ev and "demote" not in ev

    def test_migrate_defaults_adopts_when_justified(self, tmp_path) -> None:
        import postrule.defaults as D

        D.register_default_set(
            "v3",
            {
                "gate": lambda: McNemarGate(alpha=0.05, min_paired=20),
                "drift_gate": lambda: McNemarGate(),
            },
        )
        s = self._seed_default(tmp_path, "mig", n=60, target_wins=True)
        assert s.migrate_defaults(to_version="v3") is True
        assert s._default_set_version == "v3"
        assert "migrate" in _events(s)

    def test_migrate_defaults_rejected_when_unjustified(self, tmp_path) -> None:
        import postrule.defaults as D

        D.register_default_set(
            "v4",
            {
                "gate": lambda: McNemarGate(alpha=0.05, min_paired=20),
                "drift_gate": lambda: McNemarGate(),
            },
        )
        s = self._seed_default(tmp_path, "noMig", n=60, rule_wins=True)
        assert s.migrate_defaults(to_version="v4") is False  # phase not re-justified
        assert s._default_set_version == "v1"
        assert "migrate_rejected" in _events(s)

    def test_explicit_gate_is_not_default_managed(self, tmp_path) -> None:
        s = LearnedSwitch(
            rule=lambda _x: "a",
            name="exp",
            starting_phase=Phase.RULE,
            gate=McNemarGate(alpha=0.01, min_paired=20),
            storage=FileStorage(str(tmp_path)),
            persist=True,
        )
        assert s._gate_is_default is False
        assert s.migrate_defaults(to_version="v3") is False  # operator owns the gate


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


# ---------------------------------------------------------------------------
# Labels change + rule change (added-labels / rule-edit graduation integrity)
# ---------------------------------------------------------------------------
class TestLabelsAndRuleSwap:
    def test_labels_id_in_identity_and_changed(self) -> None:
        base = SignalSignature("j", ("McNemarGate", 0.01, 200), None, None)
        added = SignalSignature(
            "j", ("McNemarGate", 0.01, 200), None, None, labels_id=(("c", None),)
        )
        assert base.core_identity() != added.core_identity()
        assert added.changed_components(base) == ["labels"]

    def test_rule_version_in_identity_drift_excluded(self) -> None:
        base = SignalSignature("j", ("McNemarGate", 0.01, 200), None, None)
        bumped = SignalSignature("j", ("McNemarGate", 0.01, 200), None, None, rule_version_id="v2")
        assert bumped.changed_components(base) == ["rule"]
        # drift digest is advisory-only: it must NOT move the core identity.
        drifted = SignalSignature(
            "j", ("McNemarGate", 0.01, 200), None, None, rule_drift_digest="deadbeef"
        )
        assert drifted.core_identity() == base.core_identity()
        assert drifted.changed_components(base) == []

    def test_rule_drift_digest_catches_constant_only_edit(self) -> None:
        from postrule.signals import _rule_drift_digest

        # Same opcodes, different returned constant → must differ.
        assert _rule_drift_digest(lambda _x: "a") != _rule_drift_digest(lambda _x: "b")
        # No __code__ (C callable) → None, not a crash.
        assert _rule_drift_digest(len) is None

    def _seed_with_head(self, tmp_path, name, labels):
        s = LearnedSwitch(
            rule=lambda _x: "a",
            name=name,
            starting_phase=Phase.MODEL_PRIMARY,
            gate=McNemarGate(alpha=0.01, min_paired=20),
            ml_head=_StubHead(),
            labels=labels,
            storage=FileStorage(str(tmp_path)),
            persist=True,
        )
        # target_wins → phase stays justified, isolating quarantine from demote.
        for i in range(60):
            s._storage.append_record(name, _rec(rule_right=(i < 15), model_right=(i < 55)))
        s._storage.put_state(name, "head", b"S")
        return s

    def test_added_label_quarantines_head(self, tmp_path) -> None:
        self._seed_with_head(tmp_path, "lbl", ["a", "b"])
        s2 = LearnedSwitch(
            rule=lambda _x: "a",
            name="lbl",
            starting_phase=Phase.MODEL_PRIMARY,
            gate=McNemarGate(alpha=0.01, min_paired=20),
            ml_head=_StubHead(),
            labels=["a", "b", "c"],  # added a label → classification space changed
            storage=FileStorage(str(tmp_path)),
            persist=True,
        )
        ev = _events(s2)
        assert "swap" in ev and "quarantine" in ev
        assert s2._storage.get_state("lbl", "head") is None  # stale head dropped

    def test_rule_version_bump_demotes(self, tmp_path) -> None:
        _build(
            tmp_path,
            "rv",
            Phase.MODEL_PRIMARY,
            gate=McNemarGate(alpha=0.01, min_paired=20),
            n=60,
            rule_wins=True,
        )
        # Only rule_version changes (gate identical) → swap on "rule" → demote.
        s2 = _reopen(
            tmp_path,
            "rv",
            Phase.MODEL_PRIMARY,
            gate=McNemarGate(alpha=0.01, min_paired=20),
            rule_version="v2",
        )
        assert s2.phase() is Phase.RULE
        ev = _events(s2)
        assert "swap" in ev and "demote" in ev

    def test_rule_drift_without_version_is_advisory_not_demote(self, tmp_path) -> None:
        name = "drift"
        s = LearnedSwitch(
            rule=lambda _x: "a",
            name=name,
            starting_phase=Phase.MODEL_PRIMARY,
            gate=McNemarGate(alpha=0.01, min_paired=20),
            storage=FileStorage(str(tmp_path)),
            persist=True,
        )
        # rule_wins: a real demote WOULD cascade to RULE if this were a swap —
        # proving the advisory path deliberately holds the phase instead.
        for i in range(60):
            s._storage.append_record(name, _rec(rule_right=(i < 55), model_right=(i < 15)))
        s2 = LearnedSwitch(
            rule=lambda _x: "behaviorally-different",  # edited body, NO rule_version bump
            name=name,
            starting_phase=Phase.MODEL_PRIMARY,
            gate=McNemarGate(alpha=0.01, min_paired=20),
            storage=FileStorage(str(tmp_path)),
            persist=True,
        )
        assert s2.phase() is Phase.MODEL_PRIMARY  # held — refactor never auto-demotes
        ev = _events(s2)
        assert "advisory" in ev
        assert "demote" not in ev and "swap" not in ev
