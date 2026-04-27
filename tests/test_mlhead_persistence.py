# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""ML head state persistence: switches survive restart in trained state.

Without this, every process restart re-fits the head from the verdict
log, which is fine for development and bad for production at any
non-trivial verdict-log size. The MLHead Protocol grows two optional
methods (``state_bytes``, ``load_state``); ``SklearnTextHead``
implements them; ``LearnedSwitch`` writes the sidecar after every
advance / demote and on user-invoked ``persist_head`` calls, and
rehydrates from disk at construction.
"""

from __future__ import annotations

import pytest

from dendra import LearnedSwitch, Phase, SwitchConfig
from dendra.gates import GateDecision
from dendra.ml import SklearnTextHead


def _rule(text: str) -> str:
    return "bug" if "crash" in text else "feature"


class _AlwaysAdvanceGate:
    def evaluate(self, records, current, target):
        return GateDecision(
            target_better=True,
            rationale="persistence test",
            p_value=0.0,
            paired_sample_size=999,
        )


@pytest.fixture
def chdir_to_tmp(tmp_path, monkeypatch):
    """Persistence writes under ``runtime/dendra/`` of the cwd. Sandbox."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _train_head(records=None) -> SklearnTextHead:
    """Build a trained sklearn head against a tiny corpus."""
    pytest.importorskip("sklearn")

    class FakeRec:
        def __init__(self, input, label):
            self.input = input
            self.label = label
            self.outcome = "correct"

    if records is None:
        records = [FakeRec(f"the app keeps crashing example {i}", "bug") for i in range(30)] + [
            FakeRec(f"please add new feature request number {i}", "feature") for i in range(30)
        ]
    head = SklearnTextHead(min_outcomes=10)
    head.fit(records)
    assert head._pipeline is not None, "head should have trained on the corpus"
    return head


# ---------------------------------------------------------------------------
# Round-trip: state_bytes <-> load_state preserves predictions
# ---------------------------------------------------------------------------


class TestSklearnHeadStateRoundTrip:
    def test_round_trip_preserves_predictions(self):
        h1 = _train_head()
        blob = h1.state_bytes()
        assert isinstance(blob, bytes) and len(blob) > 0

        h2 = SklearnTextHead(min_outcomes=10)
        h2.load_state(blob)

        for text in ["the app keeps crashing", "please add a new feature"]:
            p1 = h1.predict(text, ["bug", "feature"])
            p2 = h2.predict(text, ["bug", "feature"])
            assert p1.label == p2.label
            assert p1.confidence == pytest.approx(p2.confidence)

    def test_round_trip_preserves_version(self):
        h1 = _train_head()
        v1 = h1.model_version()
        blob = h1.state_bytes()
        h2 = SklearnTextHead(min_outcomes=10)
        h2.load_state(blob)
        assert h2.model_version() == v1


# ---------------------------------------------------------------------------
# Switch sidecar: written on advance, read on construction
# ---------------------------------------------------------------------------


class TestSwitchSidecarPersistence:
    def test_advance_writes_head_sidecar(self, chdir_to_tmp):
        head = _train_head()
        sw = LearnedSwitch(
            rule=_rule,
            name="persist_test",
            author="t",
            ml_head=head,
            persist=True,
            config=SwitchConfig(starting_phase=Phase.ML_SHADOW, gate=_AlwaysAdvanceGate()),
        )
        sw.advance()
        sidecar = chdir_to_tmp / "runtime" / "dendra" / "persist_test" / ".head"
        assert sidecar.exists(), "advance() should have written the head sidecar"
        assert sidecar.read_bytes() == head.state_bytes()

    def test_persist_head_writes_sidecar(self, chdir_to_tmp):
        head = _train_head()
        sw = LearnedSwitch(
            rule=_rule,
            name="manual_persist",
            author="t",
            ml_head=head,
            persist=True,
            config=SwitchConfig(starting_phase=Phase.ML_WITH_FALLBACK),
        )
        sw.persist_head()
        sidecar = chdir_to_tmp / "runtime" / "dendra" / "manual_persist" / ".head"
        assert sidecar.exists()

    def test_construction_rehydrates_head(self, chdir_to_tmp):
        # Pre-populate a sidecar from a trained head at the location the
        # switch will look.
        trained_head = _train_head()
        target_dir = chdir_to_tmp / "runtime" / "dendra" / "rehydrate_test"
        target_dir.mkdir(parents=True)
        (target_dir / ".head").write_bytes(trained_head.state_bytes())

        # Construct a switch with an *untrained* head — persistence
        # should rehydrate it before the first predict() call.
        fresh_head = SklearnTextHead(min_outcomes=10)
        assert fresh_head._pipeline is None  # untrained

        sw = LearnedSwitch(
            rule=_rule,
            name="rehydrate_test",
            author="t",
            ml_head=fresh_head,
            persist=True,
            config=SwitchConfig(starting_phase=Phase.ML_PRIMARY),
        )

        # Head is loaded on a background thread so __init__ does not
        # block; wait for the load to finish before asserting trained
        # predictions.
        assert sw.wait_until_head_loaded(timeout=5.0), "head load did not complete"

        # The same predictions the original trained head produced.
        p_trained = trained_head.predict("the app keeps crashing", ["bug", "feature"])
        p_loaded = fresh_head.predict("the app keeps crashing", ["bug", "feature"])
        assert p_loaded.label == p_trained.label
        assert p_loaded.confidence == pytest.approx(p_trained.confidence)

    def test_no_sidecar_when_persist_false(self, chdir_to_tmp):
        head = _train_head()
        sw = LearnedSwitch(
            rule=_rule,
            name="no_persist",
            author="t",
            ml_head=head,
            persist=False,
            config=SwitchConfig(starting_phase=Phase.ML_SHADOW, gate=_AlwaysAdvanceGate()),
        )
        sw.advance()
        sw.persist_head()
        sidecar = chdir_to_tmp / "runtime" / "dendra" / "no_persist" / ".head"
        assert not sidecar.exists(), "persist=False must not write any sidecar"


# ---------------------------------------------------------------------------
# Heads that do not implement persistence still work
# ---------------------------------------------------------------------------


class TestNonPersistentHeadIsDegradedNotBroken:
    """Heads without state_bytes/load_state (e.g., custom MLHead impls
    that haven't opted in) must not break advance / persist_head."""

    def test_advance_with_legacy_head_does_not_crash(self, chdir_to_tmp):
        from dendra import MLPrediction

        class LegacyHead:
            def fit(self, records):
                pass

            def predict(self, input, labels):
                return MLPrediction(label="x", confidence=0.99)

            def model_version(self):
                return "legacy"

        head = LegacyHead()
        sw = LearnedSwitch(
            rule=_rule,
            name="legacy_head",
            author="t",
            ml_head=head,
            persist=True,
            config=SwitchConfig(starting_phase=Phase.ML_SHADOW, gate=_AlwaysAdvanceGate()),
        )
        sw.advance()  # should NOT raise
        sidecar = chdir_to_tmp / "runtime" / "dendra" / "legacy_head" / ".head"
        assert not sidecar.exists(), "legacy heads should not produce a sidecar"
