# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""#148 / #145 G4 — operator-triggered head refit.

``ml_head_version`` is excluded from swap detection so routine SDK upgrades
don't churn graduated heads. The flip side: a genuinely improved head algorithm
never reaches existing heads on its own. ``refit_head()`` / ``refit_all()`` are
the explicit operator trigger — drop the persisted head so it re-fits from the
preserved verdict log on the next ML-phase serve (mirrors the #60 quarantine
drop). Offline, no account, no log loss.
"""

from __future__ import annotations

import gc

from postrule import LearnedSwitch, McNemarGate, Phase, ml_switch, refit_all
from postrule.storage import FileStorage
from postrule.telemetry import NullEmitter


def _switch_with_head(tmp_path, name):
    s = LearnedSwitch(
        rule=lambda _x: "a",
        name=name,
        starting_phase=Phase.MODEL_PRIMARY,
        gate=McNemarGate(alpha=0.05, min_paired=20),
        storage=FileStorage(str(tmp_path)),
        persist=True,
        telemetry=NullEmitter(),
    )
    s._storage.put_state(name, "head", b"trained-weights")
    return s


def _switch_no_head(tmp_path, name):
    return LearnedSwitch(
        rule=lambda _x: "a",
        name=name,
        starting_phase=Phase.RULE,
        gate=McNemarGate(alpha=0.05, min_paired=20),
        storage=FileStorage(str(tmp_path)),
        persist=True,
        telemetry=NullEmitter(),
    )


def test_refit_head_drops_persisted_head(tmp_path):
    s = _switch_with_head(tmp_path, "refit1")
    assert s._storage.get_state("refit1", "head") == b"trained-weights"
    assert s.refit_head() is True
    assert s._storage.get_state("refit1", "head") is None  # refits from log on next serve


def test_refit_head_noop_without_head(tmp_path):
    s = _switch_no_head(tmp_path, "refit_none")
    assert s.refit_head() is False


def test_refit_all_returns_refit_switches(tmp_path):
    gc.collect()
    a = _switch_with_head(tmp_path, "refit_a")
    b = _switch_no_head(tmp_path, "refit_b")  # kept alive so refit_all sees it
    refit = refit_all()
    assert "refit_a" in refit
    assert "refit_b" not in refit  # no head -> skipped
    assert a._storage.get_state("refit_a", "head") is None
    assert b.phase() is Phase.RULE


def test_refit_head_proxied_on_decorated_fn():
    @ml_switch(name="refit_proxied", starting_phase=Phase.RULE)
    def classify(_x):
        return "a"

    # No head present → False, but the proxy must exist and call through.
    assert classify.refit_head() is False
