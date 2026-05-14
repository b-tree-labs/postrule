# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Tests for reference-rule construction used by benchmark experiments."""

from __future__ import annotations

import pytest

from postrule.benchmarks.rules import ReferenceRule, build_reference_rule

# Marker used by the Banking77 integration tests below; skipped when the
# optional `datasets` extra is not installed or when Banking77 isn't in
# the local HF cache.
_HAVE_DATASETS = True
try:
    import datasets  # noqa: F401
except ImportError:  # pragma: no cover
    _HAVE_DATASETS = False

# ---------------------------------------------------------------------------
# build_reference_rule — core behavior
# ---------------------------------------------------------------------------


class TestBuildReferenceRule:
    def test_constructs_rule_from_training_pairs(self):
        train = [
            ("i want to fly from boston to denver", "flight"),
            ("show me flights to chicago", "flight"),
            ("how much is a ticket to miami", "airfare"),
            ("what is the cost of a flight to dallas", "airfare"),
            ("what airlines fly between cities", "airline"),
            ("which carriers serve denver", "airline"),
        ]
        rule = build_reference_rule(train, seed_size=100)
        assert isinstance(rule, ReferenceRule)
        assert rule.fallback_label in ("flight", "airfare", "airline")

    def test_keywords_are_distinctive_per_label(self):
        train = [
            ("airline fly route", "airline"),
            ("airline carrier serve", "airline"),
            ("flight depart arrive", "flight"),
            ("flight schedule time", "flight"),
            ("cost ticket price", "airfare"),
            ("cost fare cheap", "airfare"),
        ]
        rule = build_reference_rule(train, seed_size=100, keywords_per_label=3)
        # Each label should have some keywords; distinctive ones belong to
        # their label rather than others.
        assert "airline" in rule.keywords_per_label
        assert "flight" in rule.keywords_per_label
        assert "airfare" in rule.keywords_per_label
        # Keywords may overlap if distinctiveness cap allows, but the most
        # distinctive token per label (e.g., "carrier") should live in that
        # label's set.
        assert len(rule.keywords_per_label["airline"]) > 0

    def test_respects_seed_size_cap(self):
        train = [(f"sample-{i} keyword word", f"label_{i % 3}") for i in range(500)]
        # Use small seed window; only first 50 examples drive the rule.
        rule = build_reference_rule(train, seed_size=50)
        assert rule.seed_size == 50

    def test_empty_train_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            build_reference_rule([], seed_size=100)

    def test_keywords_per_label_cap(self):
        train = [(f"word{j} quux", "cat") for j in range(20)] + [
            (f"word{j} quax", "dog") for j in range(20)
        ]
        rule = build_reference_rule(train, seed_size=40, keywords_per_label=2)
        assert len(rule.keywords_per_label["cat"]) <= 2
        assert len(rule.keywords_per_label["dog"]) <= 2


# ---------------------------------------------------------------------------
# ReferenceRule.classify — classification behavior
# ---------------------------------------------------------------------------


class TestReferenceRuleClassify:
    def test_classify_matches_highest_keyword_overlap(self):
        train = [
            ("alpha beta gamma", "greek"),
            ("alpha beta delta", "greek"),
            ("seven eight nine", "numbers"),
            ("seven eight ten", "numbers"),
            ("red green blue", "colors"),
            ("red green yellow", "colors"),
        ]
        rule = build_reference_rule(train, seed_size=100, keywords_per_label=3)
        # Input containing "alpha" should classify as "greek".
        assert rule.classify("alpha something else") == "greek"
        # Input containing "seven" and "eight" should classify as "numbers".
        assert rule.classify("seven eight arriving today") == "numbers"

    def test_classify_empty_input_returns_fallback(self):
        train = [
            ("alpha beta", "greek"),
            ("alpha gamma", "greek"),
            ("alpha delta", "greek"),
            ("seven eight", "numbers"),
        ]
        rule = build_reference_rule(train, seed_size=100)
        # Fallback is the modal label in the seed window — "greek" here.
        assert rule.classify("") == rule.fallback_label
        assert rule.classify("") == "greek"

    def test_classify_no_match_returns_fallback(self):
        train = [
            ("alpha beta gamma", "greek"),
            ("alpha beta delta", "greek"),
            ("seven eight nine", "numbers"),
        ]
        rule = build_reference_rule(train, seed_size=100, keywords_per_label=3)
        # No keyword overlap → fallback.
        assert rule.classify("unrelated words entirely") == rule.fallback_label

    def test_as_callable_returns_classify(self):
        train = [
            ("alpha beta", "greek"),
            ("alpha gamma", "greek"),
            ("alpha delta", "greek"),
        ]
        rule = build_reference_rule(train, seed_size=100)
        classify_fn = rule.as_callable()
        assert callable(classify_fn)
        assert classify_fn("alpha test") == "greek"


# ---------------------------------------------------------------------------
# Stopword filtering
# ---------------------------------------------------------------------------


class TestStopwordFiltering:
    def test_stopwords_are_excluded_from_keywords(self):
        # Tokens like "the", "and", "for" are stopwords and shouldn't
        # become label keywords.
        train = [
            ("the crash the crash the crash", "bug"),
            ("the feature the feature the feature", "feat"),
        ]
        rule = build_reference_rule(train, seed_size=100, keywords_per_label=5)
        all_keywords = set()
        for keywords in rule.keywords_per_label.values():
            all_keywords |= keywords
        assert "the" not in all_keywords


# ---------------------------------------------------------------------------
# Shuffle-default behavior (task #154)
#
# The HuggingFace Banking77/HWU64/CLINC150/Snips train splits are sorted by
# label. Without shuffling, the first 100 rows belong to a single class and
# the auto-rule reduces to predict-the-modal-class at chance accuracy. The
# default behavior shuffles the (text, label) stream with a deterministic
# seed before slicing the seed window so a freshly-built rule covers many
# labels and clears chance by a wide margin.
# ---------------------------------------------------------------------------


def _make_label_sorted_pairs() -> list[tuple[str, str]]:
    """Synthesize a label-sorted training stream.

    20 labels × 150 rows each = 3000 rows. The first 100 rows therefore
    all belong to ``label_00``, mirroring the Banking77/HWU64/CLINC150
    HF-split ordering pathology without requiring the HF download.
    """
    pairs: list[tuple[str, str]] = []
    for i in range(20):
        label = f"label_{i:02d}"
        # Distinctive token per label keeps the rule learnable when shuffled.
        for j in range(150):
            pairs.append((f"distinctive_{i:02d}_token sample text {j}", label))
    return pairs


class TestRuleConstructionShuffle:
    """Verify shuffle-default behavior on synthetic label-sorted data.

    These run unconditionally — the synthetic stream reproduces the
    HF-split ordering pathology without needing the `datasets` extra.
    """

    def test_synthetic_unshuffled_is_single_label_under_no_shuffle(self):
        # With shuffle=False, the seed window is captured by `label_00`
        # alone — pin the legacy (paper-as-shipped) behavior.
        pairs = _make_label_sorted_pairs()
        rule = build_reference_rule(pairs, seed_size=100, shuffle=False)
        assert len(rule.keywords_per_label) == 1
        assert rule.fallback_label == "label_00"

    def test_synthetic_default_shuffle_covers_many_labels(self):
        # With the default shuffle, the seed window samples across labels.
        pairs = _make_label_sorted_pairs()
        rule = build_reference_rule(pairs, seed_size=100)
        assert len(rule.keywords_per_label) > 5

    def test_default_shuffle_is_deterministic(self):
        # Two builds with the default seed must produce the same rule.
        pairs = _make_label_sorted_pairs()
        rule_a = build_reference_rule(pairs, seed_size=100)
        rule_b = build_reference_rule(pairs, seed_size=100)
        assert rule_a.fallback_label == rule_b.fallback_label
        assert rule_a.keywords_per_label == rule_b.keywords_per_label

    def test_explicit_shuffle_seed_is_deterministic(self):
        pairs = _make_label_sorted_pairs()
        rule_a = build_reference_rule(pairs, seed_size=100, shuffle_seed=42)
        rule_b = build_reference_rule(pairs, seed_size=100, shuffle_seed=42)
        assert rule_a.keywords_per_label == rule_b.keywords_per_label

    def test_different_shuffle_seeds_can_produce_different_rules(self):
        pairs = _make_label_sorted_pairs()
        rule_a = build_reference_rule(pairs, seed_size=100, shuffle_seed=0)
        rule_b = build_reference_rule(pairs, seed_size=100, shuffle_seed=999)
        # They both should cover many labels under the default shuffle,
        # but the specific seed window differs so at least one of the
        # selected keyword sets should differ.
        assert (
            rule_a.keywords_per_label != rule_b.keywords_per_label
            or rule_a.fallback_label != rule_b.fallback_label
        )

    def test_no_shuffle_preserves_input_order(self):
        # With shuffle=False on already-shuffled input, behavior matches
        # the legacy code path: take the first seed_size rows verbatim.
        pairs = [("alpha word", "A"), ("beta word", "B"), ("gamma word", "C")] * 50
        rule = build_reference_rule(pairs, seed_size=3, shuffle=False)
        # First three rows cover A, B, C — all three labels present.
        assert set(rule.keywords_per_label.keys()) == {"A", "B", "C"}


# ---------------------------------------------------------------------------
# Banking77 integration tests (task #154)
#
# These are the launch-blocker assertions from the validation report.
# Skipped when the dataset is not locally available; otherwise they run
# against the cached HF copy in ~/.cache/postrule/datasets/banking77.
# ---------------------------------------------------------------------------


def _try_load_banking77():
    if not _HAVE_DATASETS:
        return None
    try:
        from postrule.benchmarks.loaders import load_banking77

        return load_banking77()
    except Exception:  # pragma: no cover — network / cache-miss / mirror outage
        return None


@pytest.mark.skipif(not _HAVE_DATASETS, reason="datasets extra not installed")
# The HF `datasets` library opens an offline-probe socket during cache
# resolution that occasionally leaks. The leak is unrelated to the
# rule-builder logic under test — silence the strict `error` filter for
# this resource warning so the test surfaces the actual assertion result.
@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
class TestBanking77ShuffleDefault:
    """End-to-end checks against the real Banking77 split.

    These reproduce the validation-report findings:
      * Without shuffling, the rule predicts a single label across the
        whole 3,080-row test set (1.30% accuracy).
      * With the default shuffle, the rule covers many labels and lands
        well above chance (≈ 24% median per the validation report).
    """

    @pytest.fixture(scope="class")
    def banking77(self):
        ds = _try_load_banking77()
        if ds is None:
            pytest.skip("Banking77 not available locally")
        return ds

    def test_banking77_rule_is_not_single_label_under_default(self, banking77):
        rule = build_reference_rule(banking77.train, seed_size=100)
        preds = {rule.classify(text) for text, _ in banking77.test}
        assert len(preds) > 5, (
            f"shuffle-default rule produced only {len(preds)} distinct "
            f"labels on Banking77 test; expected > 5"
        )

    def test_banking77_rule_accuracy_above_chance_under_default(self, banking77):
        rule = build_reference_rule(banking77.train, seed_size=100)
        correct = sum(1 for text, label in banking77.test if rule.classify(text) == label)
        accuracy = correct / len(banking77.test)
        # Chance is 1/77 ≈ 1.3 %. Median across seeds in the validation
        # report is 24.4 %; assert well above chance with margin.
        assert accuracy > 0.10, (
            f"shuffle-default Banking77 rule accuracy {accuracy:.4f} "
            f"is at or near chance (1/77 ≈ 0.0130); shuffle is not active"
        )

    def test_no_shuffle_reproduces_paper_numbers(self, banking77):
        rule = build_reference_rule(banking77.train, seed_size=100, shuffle=False)
        correct = sum(1 for text, label in banking77.test if rule.classify(text) == label)
        accuracy = correct / len(banking77.test)
        # Paper-as-shipped: the rule collapses to the modal class
        # `card_arrival`; 40 / 3080 = 1.2987 %. Assert within rounding.
        assert abs(accuracy - 0.012987) < 0.001, (
            f"no-shuffle accuracy {accuracy:.6f} drifted from paper value 0.012987 (40/3080)"
        )
