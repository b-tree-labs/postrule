# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Reproduction lock: the AGA algorithm must regenerate the DMLR paper's claims.

The paper "When Should a Rule Learn?" (submitted to DMLR 2026-06-05) reports two
settings for Adaptive Graduated Autonomy in Section "AGA versus the fixed ladder":

  * Per-site measured (the deployable setting): AGA mean accuracy 0.839 vs the
    fixed "always graduate to ML" ladder 0.764, at a large reduction in labeled
    outcomes consumed; AGA keeps the LLM as terminal on about half the streams.
  * Cross-site predicted (leave-one-dataset-out meta-policy): realized mean
    accuracy 0.762, matching the fixed ladder rather than beating it.

This test runs the *shipped* ``postrule.aga`` algorithm over the committed,
frozen aligned tier results and asserts those numbers reproduce. It is the
parity guarantee that the implementation matches the paper's stated procedure —
if a future edit to ``aga.py`` changes the algorithm, these claims break loudly.

Accuracy invariants are pinned exactly (the chosen-terminal accuracy is stable).
Labeled cost is asserted only qualitatively: ``ml_cost`` is outcomes-to-tie the
LLM via ``crossover_outcomes``, which rides on the LLM tier's accuracy, and LLM
accuracies are not bit-reproducible (see REPRODUCE.md). The paper's specific
cost figures therefore drift with the aligned JSONs; the *direction and rough
magnitude* of the saving is the stable claim.
"""

from __future__ import annotations

import importlib.util
import json
import statistics as st
from pathlib import Path

import pytest

from postrule.aga import Tier, TierResult, oracle_terminal

REPO = Path(__file__).resolve().parents[1]
EVAL_SCRIPT = REPO / "scripts" / "evaluate_aga.py"
ALIGNED = (
    REPO
    / "docs"
    / "papers"
    / "2026-when-should-a-rule-learn"
    / "results"
    / "dmlr-text-expansion"
    / "aligned"
)
LATENCY_PROFILE = ALIGNED / "latency_profile.json"


def _load_eval_module():
    spec = importlib.util.spec_from_file_location("evaluate_aga", EVAL_SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pytestmark = pytest.mark.skipif(
    not ALIGNED.exists() or not EVAL_SCRIPT.exists(),
    reason="paper artifacts not present in this checkout",
)


@pytest.fixture(scope="module")
def streams():
    ev = _load_eval_module()
    recs = ev.load_streams()
    if not recs:
        pytest.skip("no aligned MODEL-tier results to reproduce from")
    built = []
    for rec in recs:
        char, tiers, oracle, ml_acc, ml_cost = ev.build(rec)
        acc = {t.tier: t.accuracy for t in tiers}
        cost = {t.tier: t.labeled_outcomes for t in tiers}
        built.append(
            {
                "dataset": rec["dataset"],
                "char": char,
                "oracle": oracle,
                "acc": acc,
                "cost": cost,
            }
        )
    return built


# --- Per-site measured: the paper's flagship deployable result --------------


def test_persite_stream_count(streams):
    # The paper evaluates 21 datasets/streams.
    assert len(streams) == 21


def test_persite_accuracy_matches_paper(streams):
    aga = st.mean(s["acc"][s["oracle"]] for s in streams)
    fixed = st.mean(s["acc"][Tier.ML] for s in streams)
    # Paper: AGA 0.839 vs fixed-ladder 0.764. Chosen-terminal accuracy is stable.
    assert aga == pytest.approx(0.839, abs=0.002)
    assert fixed == pytest.approx(0.764, abs=0.002)
    # The paired difference (+0.075) is AGA's headline win.
    assert aga - fixed == pytest.approx(0.075, abs=0.004)


def test_persite_cost_is_meaningfully_lower(streams):
    # Cost rides on the (non-bit-reproducible) LLM line, so assert the claim
    # qualitatively: AGA consumes far fewer labeled outcomes than the fixed
    # ladder. The paper reports >=30% fewer.
    aga = st.mean(s["cost"][s["oracle"]] for s in streams)
    fixed = st.mean(s["cost"][Tier.ML] for s in streams)
    assert aga < fixed
    assert (fixed - aga) / fixed >= 0.30


def test_persite_keeps_llm_terminal_about_half(streams):
    # Paper: "AGA keeps the LLM as the terminal tier on about half the streams."
    llm = sum(1 for s in streams if s["oracle"] in (Tier.MODEL_ZEROSHOT, Tier.MODEL_FEWSHOT))
    assert 8 <= llm <= 12  # ~half of 21


# --- Cross-site predicted: the paper's honest negative result ---------------


def test_crosssite_lodo_matches_fixed_not_beats_it(streams):
    pytest.importorskip("sklearn")
    from postrule.aga import AGAMetaPolicy

    rows = [(s["char"], s["oracle"]) for s in streams]
    realized = []
    hits = 0
    for i, s in enumerate(streams):
        train = [rows[j] for j in range(len(rows)) if j != i]
        pred = AGAMetaPolicy(epsilon=0.02).fit(train).recommend(s["char"])
        realized.append(s["acc"][pred])
        hits += int(pred == s["oracle"])
    lodo = st.mean(realized)
    fixed = st.mean(s["acc"][Tier.ML] for s in streams)
    # Paper: cross-site predicted falls to 0.762, matching the fixed ladder.
    assert lodo == pytest.approx(0.762, abs=0.01)
    assert abs(lodo - fixed) <= 0.01
    # And the predictor underperforms its own oracle (the negative result).
    assert hits / len(streams) < 0.5


# --- Latency axis: the paper's "Measured latency and cost" section ----------


def _latency_profile():
    if not LATENCY_PROFILE.exists():
        pytest.skip("latency_profile.json not present")
    return json.loads(LATENCY_PROFILE.read_text())


def test_latency_profile_matches_paper_p50_ranges():
    prof = _latency_profile()
    rule = [v["rule"]["p50_ms"] for v in prof.values()]
    tfidf = [v["ml_tfidf"]["p50_ms"] for v in prof.values()]
    minilm = [v["ml_minilm"]["p50_ms"] for v in prof.values()]
    llm = [v["model_llm"]["p50_ms"] for v in prof.values()]
    # Paper: rule 0.003 ms, task-trained model 0.1 ms, MiniLM ML 6 to 23 ms,
    # LLM 530 to 570 ms (p50). Lock the committed profile to those claims.
    assert max(rule) < 0.01
    assert all(0.08 <= t <= 0.2 for t in tfidf)
    assert min(minilm) >= 6.0 and max(minilm) <= 24.0
    assert all(525.0 <= x <= 575.0 for x in llm)
    # "rule is roughly 200,000x faster than the LLM" — order-of-magnitude check.
    assert min(llm) / max(rule) > 50_000


def test_realtime_slo_excludes_the_llm_by_construction():
    # Paper: "Under a real-time budget (for example 50 ms) the LLM is excluded
    # by construction, so latency forces graduation to local tiers." Build a
    # stream where the LLM is the most accurate tier; without an SLO AGA keeps
    # it, but a 50 ms SLO forces a local tier.
    prof = _latency_profile()
    sample = next(iter(prof.values()))
    tiers = [
        TierResult(Tier.RULE, 0.60, 100, per_call_ms=sample["rule"]["p50_ms"]),
        TierResult(Tier.ML, 0.85, 2000, per_call_ms=sample["ml_minilm"]["p50_ms"]),
        TierResult(Tier.MODEL_ZEROSHOT, 0.95, 0, per_call_ms=sample["model_llm"]["p50_ms"]),
    ]
    # Unconstrained: the significantly-more-accurate LLM is the terminal.
    assert oracle_terminal(tiers, latency_slo_ms=None) is Tier.MODEL_ZEROSHOT
    # Under a 50 ms real-time budget the LLM (~530 ms) is excluded by construction.
    chosen = oracle_terminal(tiers, latency_slo_ms=50.0)
    assert chosen in (Tier.RULE, Tier.ML)
