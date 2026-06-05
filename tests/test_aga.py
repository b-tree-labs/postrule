# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Tests for Adaptive Graduated Autonomy (AGA) — the multi-objective terminal-tier
selection that replaces the fixed always-climb-to-ML ladder.

These exercise the pure decision logic: the significance-aware cost-preferring
oracle, the hard modality/latency constraints, the ML→LLM crossover detector,
and the meta-policy's interpretable heuristic fallback (the sklearn-backed
learned path is covered separately and skipped when sklearn is absent).
"""

from __future__ import annotations

import math

import pytest

from postrule.aga import (
    AGAMetaPolicy,
    StreamCharacteristics,
    Tier,
    TierResult,
    crossover_outcomes,
    oracle_terminal,
    terminal_phase,
)
from postrule.core import Phase


def test_rule_over_chance_is_baseline_relative_to_uniform():
    # 4-class uniform chance = 0.25; a 0.50 rule is 2x chance.
    c = StreamCharacteristics(n_labels=4, rule_baseline=0.50)
    assert c.rule_over_chance == pytest.approx(2.0)
    # n_labels floored at 2 for the chance computation.
    c1 = StreamCharacteristics(n_labels=1, rule_baseline=0.50)
    assert c1.rule_over_chance == pytest.approx(1.0)


def test_wilson_halfwidth_degenerate_and_normal():
    from postrule.aga import wilson_halfwidth

    assert wilson_halfwidth(0.9, 0) == 1.0  # no samples -> maximally uncertain
    hw = wilson_halfwidth(0.8, 400)
    assert 0.0 < hw < 0.1  # a real, small band at n=400


def test_terminal_phase_mapping():
    assert terminal_phase(Tier.RULE) is Phase.RULE
    assert terminal_phase(Tier.ML) is Phase.ML_PRIMARY
    assert terminal_phase(Tier.MODEL_FEWSHOT) is Phase.MODEL_PRIMARY
    assert terminal_phase(Tier.MODEL_ZEROSHOT) is Phase.MODEL_PRIMARY


def test_oracle_empty_raises():
    with pytest.raises(ValueError, match="no tiers"):
        oracle_terminal([])


def test_oracle_prefers_cheaper_tier_within_band():
    # ML ties the LLM (within epsilon) -> pick the cheaper-to-operate ML.
    tiers = [
        TierResult(Tier.ML, accuracy=0.86, labeled_outcomes=4000),
        TierResult(Tier.MODEL_FEWSHOT, accuracy=0.87, labeled_outcomes=20),
    ]
    assert oracle_terminal(tiers, epsilon=0.02) is Tier.ML


def test_oracle_picks_llm_only_when_strictly_better():
    # LLM is >epsilon better than ML -> it earns the perpetual cost.
    tiers = [
        TierResult(Tier.ML, accuracy=0.72, labeled_outcomes=4000),
        TierResult(Tier.MODEL_FEWSHOT, accuracy=0.93, labeled_outcomes=20),
    ]
    assert oracle_terminal(tiers, epsilon=0.02) is Tier.MODEL_FEWSHOT


def test_oracle_prefers_rule_when_it_ties():
    # Rule is the cheapest rank; a near-tie rule beats everything.
    tiers = [
        TierResult(Tier.RULE, accuracy=0.95, labeled_outcomes=100),
        TierResult(Tier.ML, accuracy=0.96, labeled_outcomes=4000),
        TierResult(Tier.MODEL_FEWSHOT, accuracy=0.96, labeled_outcomes=20),
    ]
    assert oracle_terminal(tiers, epsilon=0.02) is Tier.RULE


def test_oracle_modality_filter_drops_infeasible_top_tier():
    # The most accurate tier is modality-infeasible -> excluded entirely.
    tiers = [
        TierResult(Tier.ML, accuracy=0.70, labeled_outcomes=4000, feasible=True),
        TierResult(Tier.MODEL_ZEROSHOT, accuracy=0.99, labeled_outcomes=0, feasible=False),
    ]
    assert oracle_terminal(tiers, epsilon=0.02) is Tier.ML


def test_oracle_latency_slo_filter():
    # LLM is most accurate but blows the latency SLO -> ML wins.
    tiers = [
        TierResult(Tier.ML, accuracy=0.80, labeled_outcomes=4000, per_call_ms=3.0),
        TierResult(Tier.MODEL_FEWSHOT, accuracy=0.95, labeled_outcomes=20, per_call_ms=530.0),
    ]
    assert oracle_terminal(tiers, epsilon=0.02, latency_slo_ms=50.0) is Tier.ML


def test_oracle_degraded_mode_when_all_constraints_fail():
    # No tier meets the SLO -> constraints relaxed rather than empty choice.
    tiers = [
        TierResult(Tier.ML, accuracy=0.80, labeled_outcomes=4000, per_call_ms=100.0),
        TierResult(Tier.MODEL_FEWSHOT, accuracy=0.95, labeled_outcomes=20, per_call_ms=530.0),
    ]
    chosen = oracle_terminal(tiers, epsilon=0.02, latency_slo_ms=1.0)
    assert chosen in {Tier.ML, Tier.MODEL_FEWSHOT}


def test_oracle_sample_n_widens_band_to_collapse_noise():
    # A 1.5pt edge for the LLM at small n is within Wilson noise -> tie -> cheaper ML.
    tiers = [
        TierResult(Tier.ML, accuracy=0.825, labeled_outcomes=4000),
        TierResult(Tier.MODEL_FEWSHOT, accuracy=0.84, labeled_outcomes=20),
    ]
    # epsilon alone (0.01) would pick the LLM; the n=200 Wilson band widens it to a tie.
    assert oracle_terminal(tiers, epsilon=0.01) is Tier.MODEL_FEWSHOT
    assert oracle_terminal(tiers, epsilon=0.01, sample_n=200) is Tier.ML


def test_crossover_outcomes_first_tie_and_never():
    curve = [(250, 0.60), (2000, 0.78), (8000, 0.86)]
    # LLM at 0.88: ML ties within epsilon=0.02 first at 8000.
    assert crossover_outcomes(curve, 0.88, epsilon=0.02) == 8000
    # LLM at 0.99: ML never ties within the measured curve.
    assert crossover_outcomes(curve, 0.99, epsilon=0.02) is None
    # Unsorted input is sorted internally.
    assert crossover_outcomes([(8000, 0.86), (250, 0.60)], 0.87, epsilon=0.02) == 8000


def test_meta_policy_heuristic_branches():
    policy = AGAMetaPolicy()  # unfitted -> heuristic
    # Near-optimal rule (>=1.6x chance) -> stay on the rule.
    assert policy.recommend(StreamCharacteristics(n_labels=2, rule_baseline=0.90)) is Tier.RULE
    # High-cardinality, near-chance rule (rule_over_chance <= 1.2) -> ML dominates.
    assert policy.recommend(StreamCharacteristics(n_labels=77, rule_baseline=0.013)) is Tier.ML
    # Low-cardinality, middling rule -> LLM few-shot.
    assert (
        policy.recommend(StreamCharacteristics(n_labels=2, rule_baseline=0.55))
        is Tier.MODEL_FEWSHOT
    )


def test_meta_policy_fit_single_class_falls_back_to_heuristic():
    # All rows share one label -> nothing to learn -> heuristic still serves.
    rows = [
        (StreamCharacteristics(n_labels=2, rule_baseline=0.55), Tier.MODEL_FEWSHOT),
        (StreamCharacteristics(n_labels=3, rule_baseline=0.50), Tier.MODEL_FEWSHOT),
    ]
    policy = AGAMetaPolicy().fit(rows)
    rec = policy.recommend(StreamCharacteristics(n_labels=2, rule_baseline=0.55))
    assert rec is Tier.MODEL_FEWSHOT


def test_meta_policy_learned_path_predicts_known_classes():
    pytest.importorskip("numpy")
    pytest.importorskip("sklearn")
    # Two well-separated classes; the fitted classifier should recover them.
    rows = []
    for _ in range(6):
        rows.append((StreamCharacteristics(n_labels=77, rule_baseline=0.02), Tier.ML))
        rows.append((StreamCharacteristics(n_labels=2, rule_baseline=0.55), Tier.MODEL_FEWSHOT))
    policy = AGAMetaPolicy().fit(rows)
    pred = policy.recommend(StreamCharacteristics(n_labels=77, rule_baseline=0.02))
    assert isinstance(pred, Tier)


def test_features_include_modality_one_hot():
    feats = AGAMetaPolicy._features(
        StreamCharacteristics(n_labels=4, rule_baseline=0.5, modality="audio"),
        modalities=["audio", "text"],
    )
    assert feats[0] == pytest.approx(math.log(4))  # log n_labels
    assert feats[-2:] == [1.0, 0.0]  # audio one-hot, text off
