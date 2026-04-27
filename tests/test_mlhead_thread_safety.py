# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Thread-safety contracts for ML head persistence.

Three contracts the implementation must hold:

A. ``advance()``'s head save runs *outside* ``self._lock``: a slow
   pickle.dumps on the saving thread must not block concurrent
   classify() callers waiting on the phase-mutation lock.
B. Persisted heads load *lazily* on a background thread:
   ``LearnedSwitch.__init__`` must return promptly even when the
   sidecar takes seconds to deserialize. Callers that need trained
   state before serving the first request use
   ``wait_until_head_loaded(timeout)``.
C. ``refit()`` is serialized against ``persist_head()`` /
   ``advance()``-driven saves via a per-head lock so concurrent
   training and serialization cannot tear the pickled blob.
"""
from __future__ import annotations

import threading
import time

import pytest

from dendra import LearnedSwitch, MLPrediction, Phase, SwitchConfig
from dendra.gates import GateDecision


def _rule(text: str) -> str:
    return "x"


class _AlwaysAdvanceGate:
    def evaluate(self, records, current, target):
        return GateDecision(
            target_better=True,
            rationale="thread-safety test",
            p_value=0.0,
            paired_sample_size=999,
        )


class _SlowSaveHead:
    """Head whose state_bytes() blocks for a configurable duration.

    Lets us measure whether a save running on one thread blocks
    classify() running on another. Records timing so the assertions
    can be expressed in real terms.
    """

    def __init__(self, save_duration_s: float = 0.5) -> None:
        self.save_duration_s = save_duration_s
        self.predict_calls = 0
        self.state_bytes_started = threading.Event()
        self.state_bytes_finished = threading.Event()
        self._pipeline = "trained"  # truthy so predict returns something

    def fit(self, records):
        return None

    def predict(self, input, labels):
        self.predict_calls += 1
        return MLPrediction(label="x", confidence=0.99)

    def model_version(self):
        return "slow-save-head"

    def state_bytes(self) -> bytes:
        self.state_bytes_started.set()
        time.sleep(self.save_duration_s)
        self.state_bytes_finished.set()
        return b"snapshot"

    def load_state(self, blob: bytes) -> None:
        pass


class _SlowLoadHead:
    """Head whose load_state() blocks for a configurable duration.

    Lets us measure whether construction blocks on the load.
    """

    def __init__(self, load_duration_s: float = 0.5) -> None:
        self.load_duration_s = load_duration_s
        self.load_started = threading.Event()
        self.load_finished = threading.Event()
        self._loaded = False

    def fit(self, records):
        return None

    def predict(self, input, labels):
        return MLPrediction(label="x", confidence=0.99)

    def model_version(self):
        return "slow-load-head"

    def state_bytes(self) -> bytes:
        return b"snapshot"

    def load_state(self, blob: bytes) -> None:
        self.load_started.set()
        time.sleep(self.load_duration_s)
        self._loaded = True
        self.load_finished.set()


@pytest.fixture
def chdir_to_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Contract A: advance() save runs outside self._lock
# ---------------------------------------------------------------------------


class TestAdvanceSaveDoesNotBlockClassify:
    def test_concurrent_classify_runs_during_save(self, chdir_to_tmp):
        head = _SlowSaveHead(save_duration_s=0.4)
        sw = LearnedSwitch(
            rule=_rule,
            name="advance_save_lockfree",
            author="t",
            ml_head=head,
            persist=True,
            config=SwitchConfig(starting_phase=Phase.ML_SHADOW, gate=_AlwaysAdvanceGate()),
        )

        # Trigger advance() on a worker. The save runs synchronously
        # but *outside* self._lock, so a concurrent classify must be
        # able to proceed while state_bytes() is sleeping.
        adv_thread = threading.Thread(target=sw.advance)
        adv_thread.start()

        # Wait until the save is in progress.
        assert head.state_bytes_started.wait(timeout=2.0), "save did not start"
        assert not head.state_bytes_finished.is_set(), (
            "save finished too fast for the test to be meaningful"
        )

        # classify() must succeed *while* the save is still running.
        # If the save held self._lock, this would block until
        # state_bytes() returned.
        t0 = time.monotonic()
        result = sw.classify("anything")
        elapsed = time.monotonic() - t0

        # Save is still running.
        assert not head.state_bytes_finished.is_set()
        # And classify completed in well under the save duration.
        assert elapsed < 0.2, (
            f"classify() blocked on advance()'s save (took {elapsed:.3f}s, "
            f"save_duration_s={head.save_duration_s})"
        )
        assert result.label == "x"
        adv_thread.join(timeout=3.0)


# ---------------------------------------------------------------------------
# Contract B: head loads on a background thread; __init__ does not block
# ---------------------------------------------------------------------------


class TestLazyLoadDoesNotBlockConstruction:
    def test_construction_returns_before_load_completes(self, chdir_to_tmp):
        # Pre-populate a sidecar so there's something for load_state to load.
        sidecar_dir = chdir_to_tmp / "runtime" / "dendra" / "lazy_load_test"
        sidecar_dir.mkdir(parents=True)
        (sidecar_dir / ".head").write_bytes(b"snapshot")

        head = _SlowLoadHead(load_duration_s=0.5)

        t0 = time.monotonic()
        sw = LearnedSwitch(
            rule=_rule,
            name="lazy_load_test",
            author="t",
            ml_head=head,
            persist=True,
            config=SwitchConfig(starting_phase=Phase.ML_PRIMARY),
        )
        construction_elapsed = time.monotonic() - t0

        # Construction returns promptly; the slow load_state runs on
        # a background thread.
        assert construction_elapsed < 0.2, (
            f"__init__ blocked on slow load_state (took {construction_elapsed:.3f}s, "
            f"load_duration_s={head.load_duration_s})"
        )

        # Wait for the background load to actually start (small race
        # window between __init__ return and the daemon thread getting
        # scheduled).
        assert head.load_started.wait(timeout=1.0), "background load did not start"

        # wait_until_head_loaded blocks until the load thread completes.
        assert sw.wait_until_head_loaded(timeout=2.0)
        assert head.load_finished.is_set()
        assert head._loaded is True

    def test_wait_returns_immediately_for_non_persistent_switch(self):
        sw = LearnedSwitch(
            rule=_rule,
            name="no_persist_no_wait",
            author="t",
            persist=False,
            config=SwitchConfig(starting_phase=Phase.RULE),
        )
        # No load thread to wait on; should return True immediately.
        t0 = time.monotonic()
        assert sw.wait_until_head_loaded(timeout=0.5) is True
        assert time.monotonic() - t0 < 0.05

    def test_wait_returns_immediately_for_legacy_head(self, chdir_to_tmp):
        # Legacy head doesn't implement load_state → no background
        # thread spawned → wait returns immediately.
        class _Legacy:
            def fit(self, r):
                pass

            def predict(self, i, lbl):
                return MLPrediction(label="x", confidence=0.5)

            def model_version(self):
                return "legacy"

        sw = LearnedSwitch(
            rule=_rule,
            name="legacy_no_wait",
            author="t",
            ml_head=_Legacy(),
            persist=True,
            config=SwitchConfig(starting_phase=Phase.ML_PRIMARY),
        )
        t0 = time.monotonic()
        assert sw.wait_until_head_loaded(timeout=0.5) is True
        assert time.monotonic() - t0 < 0.05


# ---------------------------------------------------------------------------
# Contract C: refit() is serialized with state_bytes via head_lock
# ---------------------------------------------------------------------------


class TestRefitAndPersistAreSerialized:
    def test_concurrent_refit_and_persist_do_not_tear_state(self, chdir_to_tmp):
        # Track ordering: every state_bytes() call must observe the
        # most-recent fit's pipeline value, never an in-flight torn
        # state.

        class _SerializedHead:
            def __init__(self):
                self._pipeline = 0
                self.fit_calls = 0
                self.state_bytes_observations: list[int] = []

            def fit(self, records):
                self.fit_calls += 1
                # Simulate a slow fit that mutates state across the
                # sleep — anything checking state mid-fit would see
                # an inconsistent value.
                self._pipeline = -1  # "torn" sentinel
                time.sleep(0.05)
                self._pipeline = self.fit_calls

            def predict(self, input, labels):
                return MLPrediction(label="x", confidence=0.5)

            def model_version(self):
                return f"sh-{self._pipeline}"

            def state_bytes(self) -> bytes:
                self.state_bytes_observations.append(self._pipeline)
                return f"snapshot-{self._pipeline}".encode()

            def load_state(self, blob):
                pass

        head = _SerializedHead()
        sw = LearnedSwitch(
            rule=_rule,
            name="refit_persist_serialized",
            author="t",
            ml_head=head,
            persist=True,
            config=SwitchConfig(starting_phase=Phase.ML_PRIMARY),
        )
        sw.wait_until_head_loaded(timeout=1.0)

        # Multiple refit() and persist_head() calls in parallel.
        threads = []
        for _ in range(4):
            threads.append(threading.Thread(target=lambda: sw.refit([])))
            threads.append(threading.Thread(target=lambda: sw.persist_head()))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        # No state_bytes call observed the torn -1 sentinel.
        assert -1 not in head.state_bytes_observations, (
            f"persist_head observed a torn pipeline mid-fit: "
            f"{head.state_bytes_observations}"
        )
