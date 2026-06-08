# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: LicenseRef-BSL-1.1
#
# Licensed under the Business Source License 1.1 (the "License").
# You may not use this file except in compliance with the License.
# You may obtain a copy of the License at LICENSE-BSL in the
# repository root, or at https://mariadb.com/bsl11/.
#
# Change Date:    2030-05-01
# Change License: Apache License, Version 2.0

"""Tests for the hosted-API verdict telemetry pipe.

Covered behaviors:

- Signed-out users emit nothing; the cloud emitter is not installed
  on import-time autoconfig and is not picked up by fresh switches.
- Signed-in users emit one POST per ``record_verdict`` with the
  expected payload shape (no inputs, no labels, no metadata).
- ``POSTRULE_NO_TELEMETRY=1`` short-circuits at the default-emitter
  level, regardless of credentials presence.
- A transport failure or 5xx response never raises into the caller.
- The token-bucket rate limiter drops over-budget events cleanly
  and increments ``dropped_rate_limited``.
- ``_per_classifier_correct`` is correct on the four-cell truth
  table (output is None / outcome unknown / output matches label /
  output differs).
- The atomic-increment guarantee of ``usage_metrics`` is covered by
  the server-side vitest suite; here we only verify the SDK
  enqueues one event per call (the server keeps the counter
  honest).
"""

from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from postrule import LearnedSwitch, Verdict, ml_switch
from postrule.cloud.verdict_telemetry import (
    CloudVerdictEmitter,
    maybe_install,
    uninstall,
)
from postrule.core import _per_classifier_correct
from postrule.telemetry import (
    NullEmitter,
    get_default_emitter,
    register_default_emitter,
    reset_default_emitter,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rule(ticket: dict) -> str:
    return "bug" if "crash" in (ticket.get("title", "") or "").lower() else "feature"


class _RecordingSender:
    """Capture POSTs in-process; replaces the urllib sender for tests."""

    def __init__(self, succeed: bool = True) -> None:
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._succeed = succeed

    def post(self, payload: dict[str, Any]) -> bool:
        with self._lock:
            self.calls.append(payload)
        return self._succeed


def _drain(emitter: CloudVerdictEmitter, timeout: float = 1.0) -> None:
    """Wait for the sender thread to drain the queue."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if emitter._queue.empty() and (emitter.sent + emitter.failed) >= emitter.queued:
            return
        time.sleep(0.005)


@pytest.fixture(autouse=True)
def _restore_default_emitter():
    """Every test starts with NullEmitter as the default. Restore after."""
    reset_default_emitter()
    yield
    reset_default_emitter()


# ---------------------------------------------------------------------------
# _per_classifier_correct — truth-table
# ---------------------------------------------------------------------------


class TestPerClassifierCorrect:
    def test_output_none_returns_none(self):
        assert _per_classifier_correct(None, "bug", "correct") is None

    def test_outcome_unknown_returns_none(self):
        assert _per_classifier_correct("bug", "bug", "unknown") is None

    def test_output_differs_returns_none(self):
        # We don't know if the alternative label was the right one.
        assert _per_classifier_correct("feature", "bug", "correct") is None
        assert _per_classifier_correct("feature", "bug", "incorrect") is None

    def test_output_matches_correct(self):
        assert _per_classifier_correct("bug", "bug", "correct") is True

    def test_output_matches_incorrect(self):
        assert _per_classifier_correct("bug", "bug", "incorrect") is False


# ---------------------------------------------------------------------------
# Default-emitter resolution + env-var opt-out
# ---------------------------------------------------------------------------


class TestDefaultEmitterResolution:
    def test_default_is_null_emitter(self):
        em = get_default_emitter()
        assert isinstance(em, NullEmitter)

    def test_registered_factory_overrides(self):
        sentinel = NullEmitter()
        register_default_emitter(lambda: sentinel)
        assert get_default_emitter() is sentinel

    def test_env_var_short_circuits(self, monkeypatch):
        called = {"n": 0}

        def factory():
            called["n"] += 1
            return NullEmitter()

        register_default_emitter(factory)
        monkeypatch.setenv("POSTRULE_NO_TELEMETRY", "1")
        em = get_default_emitter()
        assert isinstance(em, NullEmitter)
        assert called["n"] == 0  # factory never consulted

    def test_env_var_falsy_values_pass_through(self, monkeypatch):
        sentinel = NullEmitter()
        register_default_emitter(lambda: sentinel)
        for v in ("0", "false", "no", "off", ""):
            monkeypatch.setenv("POSTRULE_NO_TELEMETRY", v)
            assert get_default_emitter() is sentinel

    def test_factory_exception_falls_back_to_null(self):
        def broken():
            raise RuntimeError("install glitch")

        register_default_emitter(broken)
        em = get_default_emitter()
        # No exception, just NullEmitter.
        assert isinstance(em, NullEmitter)

    def test_reset_returns_to_null(self):
        register_default_emitter(lambda: NullEmitter())
        reset_default_emitter()
        # Default factory is back; should still produce NullEmitter.
        assert isinstance(get_default_emitter(), NullEmitter)


# ---------------------------------------------------------------------------
# maybe_install — gate on auth + env var
# ---------------------------------------------------------------------------


class TestMaybeInstall:
    def test_signed_out_does_not_install(self):
        em = maybe_install(auth_lookup=lambda: None)
        assert em is None
        # The default emitter is still NullEmitter.
        assert isinstance(get_default_emitter(), NullEmitter)

    def test_no_api_key_in_creds_does_not_install(self):
        em = maybe_install(auth_lookup=lambda: {"email": "x@y", "api_key": None})
        assert em is None

    def test_signed_in_installs_and_registers(self):
        creds = {"api_key": "prul_live_abc", "email": "ben@example"}  # pragma: allowlist secret
        em = maybe_install(api_url="http://localhost:8787", auth_lookup=lambda: creds)
        assert isinstance(em, CloudVerdictEmitter)
        assert get_default_emitter() is em
        em.close(timeout=0.1)

    def test_env_var_blocks_install(self, monkeypatch):
        monkeypatch.setenv("POSTRULE_NO_TELEMETRY", "1")
        creds = {"api_key": "prul_live_abc"}  # pragma: allowlist secret
        em = maybe_install(auth_lookup=lambda: creds)
        assert em is None
        assert isinstance(get_default_emitter(), NullEmitter)

    def test_uninstall_restores_null(self):
        creds = {"api_key": "prul_live_abc"}  # pragma: allowlist secret
        maybe_install(auth_lookup=lambda: creds)
        uninstall()
        assert isinstance(get_default_emitter(), NullEmitter)

    def test_auth_lookup_exception_treated_as_signed_out(self):
        def boom():
            raise OSError("creds file corrupt")

        em = maybe_install(auth_lookup=boom)
        assert em is None
        assert isinstance(get_default_emitter(), NullEmitter)

    def test_signed_in_with_telemetry_off_does_not_install(self):
        # Server-side preference (cached in ~/.postrule/credentials at
        # `postrule login` time) was toggled off via /dashboard/settings.
        creds = {
            "api_key": "prul_live_abc",  # pragma: allowlist secret
            "email": "ben@example",
            "telemetry_enabled": False,
        }
        em = maybe_install(api_url="http://localhost:8787", auth_lookup=lambda: creds)
        assert em is None
        assert isinstance(get_default_emitter(), NullEmitter)

    def test_signed_in_with_telemetry_on_installs(self):
        creds = {
            "api_key": "prul_live_abc",  # pragma: allowlist secret
            "email": "ben@example",
            "telemetry_enabled": True,
        }
        em = maybe_install(api_url="http://localhost:8787", auth_lookup=lambda: creds)
        assert isinstance(em, CloudVerdictEmitter)
        em.close(timeout=0.1)


# ---------------------------------------------------------------------------
# CloudVerdictEmitter — payload shape, queue, sender thread
# ---------------------------------------------------------------------------


class TestCloudVerdictEmitterPayload:
    def _make(self, sender: _RecordingSender) -> CloudVerdictEmitter:
        return CloudVerdictEmitter(
            api_url="http://localhost:8787",
            bearer_token="prul_live_test",  # pragma: allowlist secret
            sender=sender,
        )

    def test_emit_outcome_posts_minimal_payload(self):
        sender = _RecordingSender()
        em = self._make(sender)
        try:
            em.emit(
                "outcome",
                {
                    "switch": "triage",
                    "outcome": "correct",
                    "source": "rule",
                    "phase": "P0",
                    "label": "bug",
                    "rule_output": "bug",
                    "model_output": None,
                    "ml_output": None,
                    "rule_correct": True,
                    "model_correct": None,
                    "ml_correct": None,
                },
            )
            _drain(em)
            assert len(sender.calls) == 1
            wire = sender.calls[0]
            assert wire["switch_name"] == "triage"
            assert wire["phase"] == "P0"
            assert wire["rule_correct"] is True
            assert "model_correct" not in wire  # None is dropped
            assert "ml_correct" not in wire
            # PII / data leakage check.
            for forbidden in (
                "label",
                "ground_truth",
                "rule_output",
                "model_output",
                "ml_output",
                "input",
                "metadata",
            ):
                assert forbidden not in wire
            # Idempotency key shipped.
            assert "request_id" in wire and len(wire["request_id"]) >= 16
        finally:
            em.close(timeout=0.1)

    def test_classify_events_are_dropped(self):
        sender = _RecordingSender()
        em = self._make(sender)
        try:
            em.emit("classify", {"switch": "triage", "phase": "P0"})
            em.emit("dispatch", {"switch": "triage", "phase": "P0"})
            _drain(em, timeout=0.2)
            assert sender.calls == []
        finally:
            em.close(timeout=0.1)

    def test_emit_lifecycle_posts_metadata_only(self):
        # #145 G1 — lifecycle events ship the transition metadata (so the
        # console can show resets/migrations) but NOT the reason or signatures.
        sender = _RecordingSender()
        em = self._make(sender)
        try:
            em.emit(
                "lifecycle",
                {
                    "ts": 1234.5,
                    "event": "migrate_reset",
                    "switch": "triage",
                    "phase_before": "MODEL_PRIMARY",
                    "phase_after": "RULE",
                    "reason": "secret internal reason text",
                    "sig_before": {"gate": "x"},
                    "sig_after": {"gate": "y"},
                },
            )
            _drain(em)
            assert len(sender.calls) == 1
            wire = sender.calls[0]
            assert wire["kind"] == "lifecycle"
            assert wire["switch_name"] == "triage"
            assert wire["event"] == "migrate_reset"
            assert wire["phase_before"] == "MODEL_PRIMARY"
            assert wire["phase_after"] == "RULE"
            assert wire["ts"] == 1234.5
            assert "request_id" in wire and len(wire["request_id"]) >= 16
            # No reason / signatures / inputs leak.
            for forbidden in ("reason", "sig_before", "sig_after", "input", "label"):
                assert forbidden not in wire
        finally:
            em.close(timeout=0.1)

    def test_lifecycle_without_switch_or_event_is_dropped(self):
        sender = _RecordingSender()
        em = self._make(sender)
        try:
            em.emit("lifecycle", {"event": "migrate", "phase_after": "RULE"})  # no switch
            em.emit("lifecycle", {"switch": "triage", "phase_after": "RULE"})  # no event
            _drain(em, timeout=0.2)
            assert sender.calls == []
        finally:
            em.close(timeout=0.1)

    def test_emit_failure_absorbs_exception(self):
        # Sender that always raises.
        bad_sender = MagicMock()
        bad_sender.post.side_effect = RuntimeError("network down")
        em = CloudVerdictEmitter(
            api_url="http://localhost:8787",
            bearer_token="prul_live_test",  # pragma: allowlist secret
            sender=bad_sender,
        )
        try:
            for _ in range(5):
                em.emit(
                    "outcome",
                    {
                        "switch": "t",
                        "outcome": "correct",
                        "rule_correct": True,
                        "phase": "P0",
                    },
                )
            _drain(em)
            assert em.failed == 5
            assert em.sent == 0
        finally:
            em.close(timeout=0.1)

    def test_5xx_response_does_not_raise(self):
        sender = _RecordingSender(succeed=False)
        em = self._make(sender)
        try:
            em.emit("outcome", {"switch": "t", "outcome": "correct", "phase": "P0"})
            _drain(em)
            assert em.failed == 1
            assert em.sent == 0
        finally:
            em.close(timeout=0.1)


class TestCloudVerdictEmitterRateLimit:
    def test_burst_over_capacity_drops(self):
        sender = _RecordingSender()
        em = CloudVerdictEmitter(
            api_url="http://localhost:8787",
            bearer_token="x",
            sender=sender,
            rate_limit_burst=5,
            rate_limit_per_second=0.001,  # essentially-zero refill in test window
            start_thread=False,  # don't drain; we want to count drops
        )
        # First 5 fit in the burst budget; the rest are rate-limited.
        for _ in range(20):
            em.emit("outcome", {"switch": "t", "outcome": "correct", "phase": "P0"})
        assert em.queued == 5
        assert em.dropped_rate_limited == 15

    def test_queue_full_drop_oldest(self):
        sender = _RecordingSender()
        em = CloudVerdictEmitter(
            api_url="http://localhost:8787",
            bearer_token="x",
            sender=sender,
            queue_capacity=3,
            rate_limit_burst=100,
            rate_limit_per_second=1000,
            start_thread=False,
        )
        for i in range(10):
            em.emit("outcome", {"switch": f"s{i}", "outcome": "correct", "phase": "P0"})
        # Three slots, 10 inserts → 7 dropped to make room.
        assert em.queued == 10  # producer attempted 10 enqueues
        assert em.dropped_queue_full == 7
        # Final queue size capped at capacity.
        assert em._queue.qsize() == 3


# ---------------------------------------------------------------------------
# End-to-end via LearnedSwitch — make sure the default-emitter
# resolution actually wires up.
# ---------------------------------------------------------------------------


class TestEndToEndDefaultEmitter:
    def test_signed_in_switch_emits_to_cloud(self):
        sender = _RecordingSender()
        emitter = CloudVerdictEmitter(
            api_url="http://localhost:8787",
            bearer_token="x",
            sender=sender,
        )
        try:
            register_default_emitter(lambda: emitter)
            # Construct a switch with NO explicit telemetry= argument.
            s = LearnedSwitch(name="triage", rule=_rule, author="alice")
            s.classify({"title": "crash"})
            s.record_verdict(input={"title": "crash"}, label="bug", outcome=Verdict.CORRECT.value)
            _drain(emitter)
            assert len(sender.calls) == 1
            wire = sender.calls[0]
            assert wire["switch_name"] == "triage"
            # rule_correct True because rule_output == label AND verdict correct.
            assert wire["rule_correct"] is True
            assert wire["phase"] == "P0"
        finally:
            emitter.close(timeout=0.1)

    def test_explicit_telemetry_overrides_default(self):
        # If the user passes telemetry=NullEmitter(), the cloud bridge
        # is bypassed even when registered as the default.
        sender = _RecordingSender()
        emitter = CloudVerdictEmitter(
            api_url="http://localhost:8787",
            bearer_token="x",
            sender=sender,
        )
        try:
            register_default_emitter(lambda: emitter)
            s = LearnedSwitch(name="triage", rule=_rule, author="alice", telemetry=NullEmitter())
            s.classify({"title": "crash"})
            s.record_verdict(input={"title": "crash"}, label="bug", outcome=Verdict.CORRECT.value)
            _drain(emitter, timeout=0.2)
            assert sender.calls == []
        finally:
            emitter.close(timeout=0.1)

    def test_env_var_opt_out_at_switch_construction(self, monkeypatch):
        sender = _RecordingSender()
        emitter = CloudVerdictEmitter(
            api_url="http://localhost:8787",
            bearer_token="x",
            sender=sender,
        )
        try:
            register_default_emitter(lambda: emitter)
            monkeypatch.setenv("POSTRULE_NO_TELEMETRY", "1")
            # Construct a switch under the opt-out env var; should
            # fall back to NullEmitter despite the registered factory.
            s = LearnedSwitch(name="triage", rule=_rule, author="alice")
            s.record_verdict(input={"title": "crash"}, label="bug", outcome=Verdict.CORRECT.value)
            _drain(emitter, timeout=0.2)
            assert sender.calls == []
        finally:
            emitter.close(timeout=0.1)


# ---------------------------------------------------------------------------
# Backward-compat sanity: existing in-process emitters (ListEmitter) still
# capture the enriched payload shape.
# ---------------------------------------------------------------------------


class TestEmitterStatsAndFlush:
    """#71 — operator-visible delivery counters + reliable drain."""

    def _make(self, sender: _RecordingSender, **kw: Any) -> CloudVerdictEmitter:
        return CloudVerdictEmitter(
            api_url="http://localhost:8787",
            bearer_token="x",
            sender=sender,
            **kw,
        )

    def test_stats_snapshot_shape(self):
        em = self._make(_RecordingSender(), start_thread=False)
        s = em.stats()
        assert set(s) == {
            "queued",
            "sent",
            "dropped_rate_limited",
            "dropped_queue_full",
            "failed",
        }
        assert all(v == 0 for v in s.values())

    def test_flush_drains_so_sent_equals_recorded(self):
        sender = _RecordingSender()
        em = self._make(sender)
        try:
            for _ in range(40):
                em.emit("outcome", {"switch": "t", "outcome": "correct", "phase": "P0"})
            assert em.flush(5.0) is True
            assert em.stats()["sent"] == 40
            assert em.queued == 40
            assert len(sender.calls) == 40
        finally:
            em.close(timeout=0.1)

    def test_flush_true_when_already_empty(self):
        em = self._make(_RecordingSender())
        try:
            assert em.flush(0.5) is True
        finally:
            em.close(timeout=0.1)

    def test_flush_drains_after_queue_overflow_eviction(self):
        # Evicted items must be task_done()'d or flush() would hang on
        # unfinished_tasks forever. Produce overflow deterministically with
        # the sender thread off, then start it and confirm a clean drain.
        sender = _RecordingSender()
        em = self._make(
            sender,
            queue_capacity=3,
            rate_limit_burst=1000,
            rate_limit_per_second=10000,
            start_thread=False,
        )
        try:
            for i in range(20):
                em.emit("outcome", {"switch": f"s{i}", "outcome": "correct", "phase": "P0"})
            assert em.dropped_queue_full == 17
            assert em._queue.unfinished_tasks == 3  # eviction accounting consistent
            em._start_sender()
            assert em.flush(5.0) is True
            assert em._queue.unfinished_tasks == 0
            assert em.sent == 3
        finally:
            em.close(timeout=0.1)

    def test_first_drop_warns_once_on_stderr(self, capsys):
        em = self._make(
            _RecordingSender(),
            rate_limit_burst=1,
            rate_limit_per_second=0.001,
            start_thread=False,
        )
        for _ in range(5):
            em.emit("outcome", {"switch": "t", "outcome": "correct", "phase": "P0"})
        err = capsys.readouterr().err
        assert err.count("verdict telemetry dropped") == 1  # one-shot, not per-drop
        assert em.dropped_rate_limited == 4


class TestWrapperTelemetryAccessors:
    """#71 — telemetry_stats() / flush() surfaced on the @ml_switch wrapper."""

    def test_telemetry_stats_reports_cloud_counters(self):
        sender = _RecordingSender()
        emitter = CloudVerdictEmitter(
            api_url="http://localhost:8787", bearer_token="x", sender=sender
        )
        try:
            register_default_emitter(lambda: emitter)

            @ml_switch(labels=["bug", "feature"])
            def triage(t: dict) -> str:
                return "bug" if "crash" in (t.get("title") or "") else "feature"

            triage.record_verdict(input={"title": "crash"}, label="bug", outcome="correct")
            assert triage.flush(5.0) is True
            stats = triage.telemetry_stats()
            assert stats["queued"] == 1
            assert stats["sent"] == 1
        finally:
            emitter.close(timeout=0.1)

    def test_stats_zero_and_flush_noop_when_signed_out(self):
        # Default emitter is NullEmitter (no async send queue).
        @ml_switch(labels=["bug", "feature"])
        def triage(t: dict) -> str:
            return "feature"

        assert triage.telemetry_stats() == {
            "queued": 0,
            "sent": 0,
            "dropped_rate_limited": 0,
            "dropped_queue_full": 0,
            "failed": 0,
        }
        assert triage.flush(0.1) is True


class TestEnrichedOutcomePayload:
    def test_list_emitter_sees_rule_correct(self):
        from postrule.telemetry import ListEmitter

        em = ListEmitter()
        s = LearnedSwitch(name="t", rule=_rule, author="alice", telemetry=em)
        s.classify({"title": "crash"})
        s.record_verdict(input={"title": "crash"}, label="bug", outcome=Verdict.CORRECT.value)
        outcomes = [p for n, p in em.events if n == "outcome"]
        assert len(outcomes) == 1
        p = outcomes[0]
        assert p["rule_correct"] is True
        assert p["model_correct"] is None
        assert p["ml_correct"] is None
        assert p["phase"] == "P0"
