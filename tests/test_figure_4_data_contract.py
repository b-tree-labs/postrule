# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Data contract for paper Figure 4 (paired vs unpaired p-value).

The figure's job is to show the property "paired McNemar is
consistently tighter than unpaired-z." The natural test is on the
data behind the figure: at every checkpoint with at least one
discordant pair, the paired p-value should be at most the unpaired
p-value (allowing tiny numerical slack at extreme values).

If this contract holds, the chart will land. If it doesn't, the
prose around the figure is wrong and we'd rather know now than at
review time.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from scipy.stats import binom, norm

RESULTS_DIR = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "papers"
    / "2026-when-should-a-rule-learn"
    / "results"
)
BENCHES = ["atis", "hwu64", "banking77", "clinc150"]


def _paired_mcnemar_p(rule_correct, ml_correct):
    r = np.array(rule_correct, dtype=bool)
    m = np.array(ml_correct, dtype=bool)
    b = int(np.sum(~r & m))
    c = int(np.sum(r & ~m))
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2.0 * float(binom.cdf(k, n, 0.5)))


def _unpaired_z_p(rule_correct, ml_correct):
    r = np.array(rule_correct, dtype=bool)
    m = np.array(ml_correct, dtype=bool)
    n = len(r)
    p_r = r.mean()
    p_m = m.mean()
    p_pool = (p_r + p_m) / 2.0
    se = np.sqrt(2.0 * p_pool * (1.0 - p_pool) / n)
    if se == 0:
        return 1.0 if p_m <= p_r else 0.0
    z = (p_m - p_r) / se
    return float(1.0 - norm.cdf(z))


def _load_paired(slug):
    """Load the committed paired-bench JSONL, ignoring any prelude warnings."""
    path = RESULTS_DIR / f"{slug}_paired.jsonl"
    checkpoints = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("kind") == "checkpoint":
                checkpoints.append(rec)
    return checkpoints


@pytest.mark.parametrize("slug", BENCHES)
def test_paired_p_is_at_most_unpaired_p_at_every_checkpoint(slug):
    """Per-checkpoint, paired-McNemar p-value must be no greater than
    unpaired-z p-value. The 'paired is at-least-as-tight' claim of §5.4
    cashes out at this granularity, not just at the 250-outcome
    threshold-crossing boundary."""
    checkpoints = _load_paired(slug)
    assert checkpoints, f"no checkpoints found for {slug}"

    violations: list[dict] = []
    for ck in checkpoints:
        rule = ck["rule_correct"]
        ml = ck["ml_correct"]
        paired = _paired_mcnemar_p(rule, ml)
        unpaired = _unpaired_z_p(rule, ml)

        # Allow tiny numerical slack at the extreme tails. Anything above
        # 1e-12 of slack is a real ordering inversion worth flagging.
        if paired > unpaired + 1e-12:
            violations.append(
                {
                    "training_outcomes": ck["training_outcomes"],
                    "paired_p": paired,
                    "unpaired_p": unpaired,
                    "ratio": paired / unpaired if unpaired else float("inf"),
                }
            )

    assert not violations, (
        f"{slug}: paired p-value exceeded unpaired p-value at "
        f"{len(violations)} checkpoint(s); first few: {violations[:3]}"
    )
