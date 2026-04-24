# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Bulk ingestion + reviewer round-trip + Human/Webhook verdict sources."""

from __future__ import annotations

import queue
import threading

import pytest

from dendra import (
    BulkVerdict,
    BulkVerdictSummary,
    LearnedSwitch,
    ModelPrediction,
    Phase,
    SwitchConfig,
    Verdict,
)
from dendra.verdicts import (
    CallableVerdictSource,
    HumanReviewerSource,
    WebhookVerdictSource,
)


def _rule(x):
    return f"rule-{x}"


# ---------------------------------------------------------------------------
# bulk_record_verdicts
# ---------------------------------------------------------------------------


class TestBulkRecordVerdicts:
    def test_appends_every_row(self):
        sw = LearnedSwitch(
            rule=_rule, name="bulk_basic", author="t",
            config=SwitchConfig(auto_record=False, auto_advance=False),
        )
        batch = [
            BulkVerdict(input=i, label=f"rule-{i}", outcome=Verdict.CORRECT.value)
            for i in range(10)
        ]
        s = sw.bulk_record_verdicts(batch)
        assert s.total == 10
        assert s.recorded == 10
        assert s.failed == 0
        assert len(sw.storage.load_records(sw.name)) == 10

    def test_empty_batch_is_no_op(self):
        sw = LearnedSwitch(
            rule=_rule, name="bulk_empty", author="t",
            config=SwitchConfig(auto_record=False, auto_advance=False),
        )
        s = sw.bulk_record_verdicts([])
        assert s.total == 0
        assert s.recorded == 0
        assert s.auto_advance_decision is None

    def test_failed_row_absorbed_not_propagated(self):
        """One malformed row doesn't poison the batch."""
        sw = LearnedSwitch(
            rule=_rule, name="bulk_poison", author="t",
            config=SwitchConfig(auto_record=False, auto_advance=False),
        )
        batch = [
            BulkVerdict(input=1, label="rule-1", outcome=Verdict.CORRECT.value),
            BulkVerdict(input=2, label="rule-2", outcome="BOGUS_OUTCOME"),  # bad
            BulkVerdict(input=3, label="rule-3", outcome=Verdict.CORRECT.value),
        ]
        s = sw.bulk_record_verdicts(batch)
        assert s.total == 3
        assert s.recorded == 2
        assert s.failed == 1

    def test_auto_advance_deferred_to_end_of_batch(self):
        """auto_advance fires at most once per bulk call, not on
        every interval-boundary mid-batch."""
        advance_calls = []

        class _CountingGate:
            def evaluate(self, records, current, target, /):
                from dendra.gates import GateDecision
                advance_calls.append(len(records))
                return GateDecision(advance=False, rationale="never")

        sw = LearnedSwitch(
            rule=_rule, name="bulk_autoadv", author="t",
            config=SwitchConfig(
                auto_record=False,
                auto_advance=True,
                auto_advance_interval=5,
                gate=_CountingGate(),
            ),
        )
        batch = [
            BulkVerdict(input=i, label="x", outcome=Verdict.CORRECT.value)
            for i in range(25)
        ]
        s = sw.bulk_record_verdicts(batch)
        # Without the defer, advance would fire 5 times (interval=5, total=25).
        # With it, fires exactly once at end-of-batch.
        assert len(advance_calls) == 1, (
            f"bulk should defer to one end-of-batch advance; got {len(advance_calls)}"
        )
        assert s.auto_advance_decision is not None

    def test_bulk_then_individual_resumes_normal_cadence(self):
        """After a bulk call, record_verdict auto-advance counter
        continues normally."""
        advance_calls = []

        class _CountingGate:
            def evaluate(self, records, current, target, /):
                from dendra.gates import GateDecision
                advance_calls.append(len(records))
                return GateDecision(advance=False, rationale="never")

        sw = LearnedSwitch(
            rule=_rule, name="bulk_resume", author="t",
            config=SwitchConfig(
                auto_record=False,
                auto_advance=True,
                auto_advance_interval=3,
                gate=_CountingGate(),
            ),
        )
        sw.bulk_record_verdicts(
            [BulkVerdict(input=i, label="x", outcome=Verdict.CORRECT.value)
             for i in range(2)],
        )
        advance_calls.clear()
        # Individual calls resume; counter was reset so the next 3 trigger.
        for i in range(3):
            sw.record_verdict(
                input=i, label="x", outcome=Verdict.CORRECT.value,
            )
        assert len(advance_calls) == 1


# ---------------------------------------------------------------------------
# bulk_record_verdicts_from_source
# ---------------------------------------------------------------------------


class TestBulkFromSource:
    def test_pipeline_records_source_stamp(self):
        def oracle(input, label):
            return Verdict.CORRECT if label == f"rule-{input}" else Verdict.INCORRECT

        sw = LearnedSwitch(
            rule=_rule, name="pipe_oracle", author="t",
            config=SwitchConfig(auto_record=False, auto_advance=False),
        )
        src = CallableVerdictSource(oracle, name="oracle")
        summary = sw.bulk_record_verdicts_from_source(range(5), src)
        assert summary.recorded == 5
        recs = sw.storage.load_records(sw.name)
        assert all(r.source == "callable:oracle" for r in recs)
        assert all(r.outcome == Verdict.CORRECT.value for r in recs)


# ---------------------------------------------------------------------------
# export_for_review / apply_reviews
# ---------------------------------------------------------------------------


class TestReviewerRoundTrip:
    def test_export_returns_unknown_records_only(self):
        sw = LearnedSwitch(
            rule=_rule, name="exp_unknown", author="t",
            config=SwitchConfig(auto_record=False, auto_advance=False),
        )
        # Mix of outcomes — only UNKNOWN should export.
        for i in range(5):
            sw.record_verdict(
                input=i, label=f"rule-{i}",
                outcome=Verdict.UNKNOWN.value if i % 2 == 0 else Verdict.CORRECT.value,
            )
        queue_out = sw.export_for_review()
        assert len(queue_out) == 3  # inputs 0, 2, 4
        assert all(q["classified_label"] for q in queue_out)

    def test_export_limit_respected(self):
        sw = LearnedSwitch(
            rule=_rule, name="exp_limit", author="t",
            config=SwitchConfig(auto_record=False, auto_advance=False),
        )
        for i in range(10):
            sw.record_verdict(
                input=i, label="x", outcome=Verdict.UNKNOWN.value,
            )
        assert len(sw.export_for_review(limit=3)) == 3

    def test_apply_reviews_matches_by_input_hash(self):
        sw = LearnedSwitch(
            rule=_rule, name="apply_roundtrip", author="t",
            config=SwitchConfig(auto_record=False, auto_advance=False),
        )
        for i in range(3):
            sw.record_verdict(
                input={"id": i}, label=f"rule-{i}",
                outcome=Verdict.UNKNOWN.value,
            )
        queue_out = sw.export_for_review()
        # Reviewer marks row 0 correct, row 1 incorrect, row 2 stays.
        reviews = [
            {**queue_out[0], "outcome": Verdict.CORRECT.value},
            {**queue_out[1], "outcome": Verdict.INCORRECT.value},
        ]
        s = sw.apply_reviews(reviews)
        assert s.recorded == 2
        assert s.failed == 0
        # Total records now: 3 original UNKNOWN + 2 verdict-bearing.
        records = sw.storage.load_records(sw.name)
        assert len(records) == 5
        correct = [r for r in records if r.outcome == Verdict.CORRECT.value]
        incorrect = [r for r in records if r.outcome == Verdict.INCORRECT.value]
        assert len(correct) == 1
        assert len(incorrect) == 1
        assert correct[0].source == "human-reviewer"

    def test_apply_reviews_unmatched_hash_counted_as_failed(self):
        sw = LearnedSwitch(
            rule=_rule, name="apply_miss", author="t",
            config=SwitchConfig(auto_record=False, auto_advance=False),
        )
        s = sw.apply_reviews([
            {"input_hash": "deadbeef", "outcome": Verdict.CORRECT.value},
            {"input_hash": "cafebabe", "outcome": Verdict.INCORRECT.value},
        ])
        assert s.recorded == 0
        assert s.failed == 2


# ---------------------------------------------------------------------------
# HumanReviewerSource
# ---------------------------------------------------------------------------


class TestHumanReviewerSource:
    def test_round_trip_via_queues(self):
        pending: queue.Queue = queue.Queue()
        verdicts: queue.Queue = queue.Queue()
        src = HumanReviewerSource(
            pending=pending, verdicts=verdicts, timeout=2.0, name="test",
        )

        def fake_reviewer():
            input, label = pending.get(timeout=2.0)
            verdicts.put(Verdict.CORRECT)

        t = threading.Thread(target=fake_reviewer, daemon=True)
        t.start()
        result = src.judge("x", "rule-x")
        t.join(timeout=2.0)
        assert result is Verdict.CORRECT

    def test_timeout_returns_unknown(self):
        """No reviewer on shift → UNKNOWN, not a hang."""
        src = HumanReviewerSource(timeout=0.1, name="stall")
        result = src.judge("x", "y")
        assert result is Verdict.UNKNOWN

    def test_audit_stamp_includes_name(self):
        src = HumanReviewerSource(name="ops-1")
        assert src.source_name == "human-reviewer:ops-1"

    def test_accepts_string_verdict_from_queue(self):
        """Reviewer tools coming off JSON queues typically push strings."""
        pending: queue.Queue = queue.Queue()
        verdicts: queue.Queue = queue.Queue()
        src = HumanReviewerSource(
            pending=pending, verdicts=verdicts, timeout=2.0,
        )
        verdicts.put("incorrect")
        assert src.judge("x", "y") is Verdict.INCORRECT


# ---------------------------------------------------------------------------
# WebhookVerdictSource
# ---------------------------------------------------------------------------


class TestWebhookVerdictSource:
    def test_construction_requires_httpx(self):
        pytest.importorskip("httpx")

    def _mock_httpx(self, ws, *, status=200, payload=None, raises=None):
        """Install a fake httpx.post on the source."""
        class _FakeResp:
            def __init__(self, status, payload):
                self.status_code = status
                self._payload = payload

            def raise_for_status(self):
                if self.status_code >= 400:
                    import httpx
                    raise httpx.HTTPStatusError(
                        "bad", request=None, response=self,  # type: ignore[arg-type]
                    )

            def json(self):
                if self._payload is None:
                    raise ValueError("empty body")
                return self._payload

        def post(url, **kwargs):
            if raises is not None:
                raise raises
            return _FakeResp(status, payload)

        ws._httpx.post = post  # type: ignore[attr-defined]

    def test_judge_parses_valid_response(self):
        ws = WebhookVerdictSource("http://example.invalid/verdict", timeout=1.0)
        self._mock_httpx(ws, payload={"outcome": Verdict.CORRECT.value})
        assert ws.judge("x", "y") is Verdict.CORRECT

    def test_http_failure_absorbed_as_unknown(self):
        import httpx
        ws = WebhookVerdictSource("http://example.invalid/verdict", timeout=1.0)
        self._mock_httpx(ws, raises=httpx.ConnectError("down"))
        assert ws.judge("x", "y") is Verdict.UNKNOWN

    def test_malformed_body_unknown(self):
        ws = WebhookVerdictSource("http://example.invalid/verdict", timeout=1.0)
        self._mock_httpx(ws, payload={"something_else": "nope"})
        assert ws.judge("x", "y") is Verdict.UNKNOWN

    def test_invalid_outcome_unknown(self):
        ws = WebhookVerdictSource("http://example.invalid/verdict", timeout=1.0)
        self._mock_httpx(ws, payload={"outcome": "maybe-ish"})
        assert ws.judge("x", "y") is Verdict.UNKNOWN

    def test_audit_stamp_uses_name_or_endpoint(self):
        ws1 = WebhookVerdictSource(
            "http://reviewer.example/verdict",
            timeout=1.0,
        )
        assert "reviewer.example" in ws1.source_name

        ws2 = WebhookVerdictSource(
            "http://reviewer.example/verdict",
            timeout=1.0,
            name="crm",
        )
        assert ws2.source_name == "webhook:crm"
