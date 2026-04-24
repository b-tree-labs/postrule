# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Runtime-enforced architectural guarantees — safety_critical +
breaker-state persistence (paper §7.1 promises that must survive
both direct-mutation bugs and process restarts)."""

from __future__ import annotations

import pytest

from dendra import (
    LearnedSwitch,
    MLPrediction,
    Phase,
    SwitchConfig,
)


def _rule(x):
    return "ok"


class _FlakyMLHead:
    """Always raises; used to trip the breaker."""

    def fit(self, records):
        pass

    def predict(self, input, labels):
        raise RuntimeError("down")

    def model_version(self):
        return "flaky"


class _HealthyMLHead:
    def fit(self, records):
        pass

    def predict(self, input, labels):
        return MLPrediction(label="ok", confidence=0.99)

    def model_version(self):
        return "healthy"


# ---------------------------------------------------------------------------
# #2: safety_critical runtime re-check
# ---------------------------------------------------------------------------


class TestSafetyCriticalRuntimeCheck:
    def test_direct_mutation_of_starting_phase_refused_at_classify(self):
        """Construction blocked ML_PRIMARY — runtime must refuse too.

        Construction-time checks catch the honest misconfiguration.
        A buggy caller who mutates ``config.starting_phase`` after
        construction bypasses those checks. The runtime re-check in
        ``_classify_impl`` is the last line of defense for the
        paper §7.1 architectural guarantee.
        """
        sw = LearnedSwitch(
            rule=_rule,
            name="sc_runtime",
            author="test",
            config=SwitchConfig(
                safety_critical=True,
                starting_phase=Phase.ML_WITH_FALLBACK,
                auto_record=False,
                auto_advance=False,
            ),
            ml_head=_HealthyMLHead(),
        )
        # Smuggle through a direct mutation — simulates a buggy
        # caller reaching into internals, or a future refactor that
        # moves phase into a non-frozen surface.
        sw.config.starting_phase = Phase.ML_PRIMARY

        with pytest.raises(RuntimeError, match="safety_critical=True"):
            sw.classify("anything")


# ---------------------------------------------------------------------------
# #6: breaker-state persistence across process restart
# ---------------------------------------------------------------------------


class TestBreakerPersistence:
    def test_tripped_breaker_survives_restart(self, tmp_path, monkeypatch):
        """A tripped breaker on persist=True must still be tripped on restart.

        Today the breaker flag is an instance attribute. Without
        persistence, a k8s rollout or systemd restart re-routes
        traffic through the broken ML head that tripped it. With
        persistence, restart sees the sidecar file and starts
        tripped — rule floor holds.
        """
        # Chdir into tmp_path so the default breaker-state path
        # (runtime/dendra/<name>/.breaker) lives under the test's
        # scratch directory. Restores on teardown.
        monkeypatch.chdir(tmp_path)

        sw1 = LearnedSwitch(
            rule=_rule,
            name="breaker_persist_test",
            author="test",
            persist=True,
            config=SwitchConfig(
                starting_phase=Phase.ML_PRIMARY,
                auto_record=False,
                auto_advance=False,
            ),
            ml_head=_FlakyMLHead(),
        )
        # First call trips the breaker.
        r1 = sw1.classify("x")
        assert r1.source == "rule_fallback"
        assert sw1._circuit_tripped

        # Simulate process restart: new LearnedSwitch for the same name.
        sw2 = LearnedSwitch(
            rule=_rule,
            name="breaker_persist_test",
            author="test",
            persist=True,
            config=SwitchConfig(
                starting_phase=Phase.ML_PRIMARY,
                auto_record=False,
                auto_advance=False,
            ),
            # Fresh ML head — but breaker should already be tripped
            # from the persisted state, so this never gets called.
            ml_head=_HealthyMLHead(),
        )
        assert sw2._circuit_tripped, (
            "breaker state should have been rehydrated from disk"
        )

        # And classify still falls through to the rule.
        r2 = sw2.classify("y")
        assert r2.source == "rule_fallback"

    def test_reset_circuit_breaker_clears_persisted_state(
        self, tmp_path, monkeypatch
    ):
        """Operator reset clears the breaker on disk, not just in memory."""
        monkeypatch.chdir(tmp_path)

        sw1 = LearnedSwitch(
            rule=_rule,
            name="breaker_reset_test",
            author="test",
            persist=True,
            config=SwitchConfig(
                starting_phase=Phase.ML_PRIMARY,
                auto_record=False,
                auto_advance=False,
            ),
            ml_head=_FlakyMLHead(),
        )
        sw1.classify("x")  # trip
        assert sw1._circuit_tripped

        sw1.reset_circuit_breaker()
        assert not sw1._circuit_tripped

        # New process sees the breaker as clear.
        sw2 = LearnedSwitch(
            rule=_rule,
            name="breaker_reset_test",
            author="test",
            persist=True,
            config=SwitchConfig(
                starting_phase=Phase.ML_PRIMARY,
                auto_record=False,
                auto_advance=False,
            ),
            ml_head=_HealthyMLHead(),
        )
        assert not sw2._circuit_tripped

    def test_keyboard_interrupt_propagates_through_classify(self):
        """#11: KeyboardInterrupt must NOT be absorbed by the rule floor.

        The rule-floor promise catches ordinary Exception subclasses
        (e.g. provider timeouts, network errors) and falls back to
        the rule. KeyboardInterrupt and SystemExit are operator-
        initiated control-flow signals — swallowing them leaves the
        user unable to interrupt their own process.
        """
        class _KBInterruptingMLHead:
            def fit(self, records):
                pass

            def predict(self, input, labels):
                raise KeyboardInterrupt

            def model_version(self):
                return "kb"

        sw = LearnedSwitch(
            rule=_rule,
            name="kb_test",
            author="test",
            ml_head=_KBInterruptingMLHead(),
            config=SwitchConfig(
                starting_phase=Phase.ML_WITH_FALLBACK,
                auto_record=False,
                auto_advance=False,
            ),
        )
        with pytest.raises(KeyboardInterrupt):
            sw.classify("x")

    def test_cancelled_error_falls_back_to_rule(self):
        """#11: asyncio.CancelledError (BaseException in py3.8+) should
        be absorbed by the rule floor.

        Treating CancelledError like KeyboardInterrupt would propagate
        task-level cancellation through the classifier — breaks
        FastAPI / LangGraph / LlamaIndex integrations where callers
        expect a successful rule-fallback on timeout.
        """
        import asyncio

        class _CancellingMLHead:
            def fit(self, records):
                pass

            def predict(self, input, labels):
                raise asyncio.CancelledError

            def model_version(self):
                return "cancel"

        sw = LearnedSwitch(
            rule=_rule,
            name="cancel_test",
            author="test",
            ml_head=_CancellingMLHead(),
            config=SwitchConfig(
                starting_phase=Phase.ML_WITH_FALLBACK,
                auto_record=False,
                auto_advance=False,
            ),
        )
        r = sw.classify("x")
        assert r.source == "rule_fallback"

    def test_persist_false_breaker_is_process_local(self, tmp_path, monkeypatch):
        """Without persist=True, breaker state is ephemeral by design."""
        monkeypatch.chdir(tmp_path)

        sw1 = LearnedSwitch(
            rule=_rule,
            name="breaker_mem_test",
            author="test",
            config=SwitchConfig(
                starting_phase=Phase.ML_PRIMARY,
                auto_record=False,
                auto_advance=False,
            ),
            ml_head=_FlakyMLHead(),
        )
        sw1.classify("x")
        assert sw1._circuit_tripped

        # No persistence — a "restart" starts untripped.
        sw2 = LearnedSwitch(
            rule=_rule,
            name="breaker_mem_test",
            author="test",
            config=SwitchConfig(
                starting_phase=Phase.ML_PRIMARY,
                auto_record=False,
                auto_advance=False,
            ),
            ml_head=_HealthyMLHead(),
        )
        assert not sw2._circuit_tripped
