# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: LicenseRef-BSL-1.1

"""#95 — failure-condition detector, dogfooded as a Postrule switch.

Detects data-loss / fragmentation / corruption from control-plane signals
only (no classified content), so its output is safe on an internal or a
curated public dogfooding view. The detector is itself an @ml_switch.
"""

from __future__ import annotations

import time

from postrule import Phase
from postrule.cloud.failure_detector import (
    FAILURE_LABELS,
    classify_failure,
    extract_fingerprint,
    failure_detector,
)


def _ledger(*phases_after, with_adopt_reset=False):
    """Build minimal ledger rows: a graduation then (optionally) a fresh adopt
    back at RULE (= cold-start state loss)."""
    rows = [{"event": "adopt", "phase_after": Phase.RULE.name}]
    for p in phases_after:
        rows.append({"event": "swap", "phase_after": p})
    if with_adopt_reset:
        rows.append({"event": "adopt", "phase_after": Phase.RULE.name})
    return rows


class TestFingerprint:
    def test_is_decisions_only(self) -> None:
        fp = extract_fingerprint("s", verdict_timestamps=[1.0, 2.0])
        # No field carries classified content.
        for forbidden in ("input", "label", "content", "text"):
            assert forbidden not in fp.features()

    def test_detects_verdict_gap(self) -> None:
        now = time.time()
        ts = [now - 100 * 3600] + [now - i for i in range(60)]  # one 100h+ gap, then active
        fp = extract_fingerprint("s", verdict_timestamps=ts)
        assert fp.max_verdict_gap_hours > 24
        assert fp.total_verdicts == 61

    def test_detects_unexplained_phase_reset(self) -> None:
        fp = extract_fingerprint(
            "s",
            verdict_timestamps=[1.0],
            ledger_events=_ledger("MODEL_PRIMARY", with_adopt_reset=True),
        )
        assert fp.unexplained_phase_resets == 1

    def test_no_reset_without_prior_graduation(self) -> None:
        fp = extract_fingerprint(
            "s", verdict_timestamps=[1.0], ledger_events=_ledger(with_adopt_reset=True)
        )
        assert fp.unexplained_phase_resets == 0


class TestDetectorSwitch:
    def test_labels(self) -> None:
        assert FAILURE_LABELS == ["healthy", "state_loss", "fragmentation", "corruption"]

    def test_is_a_postrule_switch(self) -> None:
        # Dogfood: the detector is a wrapped @ml_switch starting at the rule floor.
        assert failure_detector.current_phase is Phase.RULE

    def test_healthy(self) -> None:
        fp = extract_fingerprint("s", verdict_timestamps=[float(i) for i in range(10)])
        assert classify_failure(fp) == "healthy"

    def test_corruption_wins(self) -> None:
        fp = extract_fingerprint(
            "s", verdict_timestamps=[1.0], corrupt_state=True, distinct_recent_phases=3
        )
        assert classify_failure(fp) == "corruption"  # most-severe wins

    def test_state_loss_on_reset(self) -> None:
        fp = extract_fingerprint(
            "s",
            verdict_timestamps=[1.0],
            ledger_events=_ledger("ML_PRIMARY", with_adopt_reset=True),
        )
        assert classify_failure(fp) == "state_loss"

    def test_fragmentation(self) -> None:
        fp = extract_fingerprint("s", verdict_timestamps=[1.0], distinct_recent_phases=2)
        assert classify_failure(fp) == "fragmentation"

    def test_long_gap_on_active_switch_is_state_loss(self) -> None:
        now = time.time()
        ts = [now - 200 * 3600] + [now - i for i in range(60)]
        fp = extract_fingerprint("s", verdict_timestamps=ts)
        assert classify_failure(fp) == "state_loss"
