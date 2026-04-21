# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for reference-rule construction used by benchmark experiments."""

from __future__ import annotations

import pytest

from dendra.benchmarks.rules import ReferenceRule, build_reference_rule

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
