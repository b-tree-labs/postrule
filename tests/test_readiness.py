# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""#148 — local graduation-readiness probe.

``LearnedSwitch.readiness()`` answers "would this switch graduate on its
current recent evidence, and how far short is it?" — read-only, offline, no
account. It lets a low-data environment (dev/staging) validate that the
graduation machinery is wired without prod-scale data.
"""

from __future__ import annotations

import time

from postrule import GraduationReadiness, LearnedSwitch, McNemarGate, Phase, ml_switch
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


def _switch(tmp_path, name, *, phase=Phase.RULE, gate=None, n=0, model_beats_rule=False):
    s = LearnedSwitch(
        rule=lambda _x: "a",
        name=name,
        starting_phase=phase,
        gate=gate or McNemarGate(alpha=0.05, min_paired=20),
        storage=FileStorage(str(tmp_path)),
        persist=True,
        telemetry=NullEmitter(),
    )
    for i in range(n):
        if model_beats_rule:  # model right, rule wrong => RULE->MODEL_SHADOW justified
            s._storage.append_record(name, _rec(rule_right=(i < n // 4), model_right=(i < n - 5)))
    return s


def test_low_data_is_not_ready_with_shortfall(tmp_path):
    # MODEL_SHADOW -> MODEL_PRIMARY is a real decision-maker change (rule vs
    # model), so the gate can actually discriminate — unlike RULE->MODEL_SHADOW.
    s = _switch(tmp_path, "lowdata", phase=Phase.MODEL_SHADOW, n=3, model_beats_rule=True)
    r = s.readiness()
    assert isinstance(r, GraduationReadiness)
    assert r.switch == "lowdata"
    assert r.phase is Phase.MODEL_SHADOW
    assert r.target_phase is Phase.MODEL_PRIMARY
    assert r.would_advance is False
    assert r.paired_sample_size < r.min_paired
    assert r.shortfall == r.min_paired - r.paired_sample_size
    assert r.shortfall > 0


def test_sufficient_evidence_is_ready(tmp_path):
    s = _switch(tmp_path, "ready", phase=Phase.MODEL_SHADOW, n=60, model_beats_rule=True)
    r = s.readiness()
    assert r.would_advance is True
    assert r.shortfall == 0
    assert r.paired_sample_size >= r.min_paired


def test_terminal_phase_has_no_target(tmp_path):
    s = _switch(tmp_path, "terminal", phase=Phase.ML_PRIMARY)
    r = s.readiness()
    assert r.target_phase is None
    assert r.would_advance is False
    assert r.shortfall == 0


def test_readiness_does_not_mutate_phase(tmp_path):
    s = _switch(tmp_path, "nomutate", n=60, model_beats_rule=True)
    before = s.phase()
    s.readiness()
    s.readiness()
    assert s.phase() is before  # probe is read-only


def test_readiness_reports_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTRULE_ENV", "staging")
    s = _switch(tmp_path, "env", n=3, model_beats_rule=True)
    assert s.readiness().environment == "staging"


def test_readiness_proxied_on_decorated_fn():
    @ml_switch(name="proxied", starting_phase=Phase.RULE)
    def classify(_x):
        return "a"

    r = classify.readiness()
    assert isinstance(r, GraduationReadiness)
    assert r.switch == "proxied"
