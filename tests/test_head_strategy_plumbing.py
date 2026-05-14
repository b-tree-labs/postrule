# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""LearnedSwitch lazily consults a head_strategy when no ml_head is given.

Contracts:

A. ``LearnedSwitch`` accepts a ``head_strategy=...`` kwarg. If no
   ``ml_head`` is given, the switch uses the strategy to pick a head
   when one is first needed.

B. The pick is *lazy*: construction does not run the strategy. The
   strategy is consulted on first access from a phase that needs the
   head (e.g., classify at ``ML_WITH_FALLBACK`` or ``ML_PRIMARY``).

C. Passing both ``ml_head`` and ``head_strategy`` raises so the user
   isn't surprised by which one wins.

D. Once the strategy has selected a head, the switch caches it; the
   strategy is not consulted again on subsequent classifies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from postrule import (
    FixedMLHeadStrategy,
    LearnedSwitch,
    MLPrediction,
    Phase,
    SwitchConfig,
)


def _rule(text: str) -> str:
    return "x"


@dataclass
class _CountingStrategy:
    """Strategy that records every call to .select()."""

    head: Any
    calls: int = 0

    def select(self, records):
        self.calls += 1
        return self.head


class _StaticHead:
    def __init__(self, label: str = "from_strategy") -> None:
        self.label = label
        self.predict_calls = 0

    def fit(self, records):
        return None

    def predict(self, input, labels):
        self.predict_calls += 1
        return MLPrediction(label=self.label, confidence=0.99)

    def model_version(self):
        return "static-strategy-head"


# ---------------------------------------------------------------------------
# A: head_strategy kwarg on LearnedSwitch
# ---------------------------------------------------------------------------


class TestHeadStrategyKwarg:
    def test_accepts_head_strategy_when_no_ml_head(self):
        head = _StaticHead()
        strat = FixedMLHeadStrategy(head)
        sw = LearnedSwitch(
            rule=_rule,
            name="strategy_kwarg_basic",
            author="t",
            head_strategy=strat,
            config=SwitchConfig(starting_phase=Phase.RULE),
        )
        assert sw is not None

    def test_classify_at_ml_phase_uses_strategy_picked_head(self):
        head = _StaticHead(label="strategy_pick")
        strat = FixedMLHeadStrategy(head)
        sw = LearnedSwitch(
            rule=_rule,
            name="strategy_pick",
            author="t",
            head_strategy=strat,
            config=SwitchConfig(starting_phase=Phase.ML_PRIMARY),
        )
        result = sw.classify("anything")
        assert result.label == "strategy_pick"
        assert head.predict_calls == 1


# ---------------------------------------------------------------------------
# B: Lazy — strategy consulted only when head is first needed
# ---------------------------------------------------------------------------


class TestHeadStrategyIsLazy:
    def test_strategy_not_called_at_construction(self):
        strat = _CountingStrategy(head=_StaticHead())
        LearnedSwitch(
            rule=_rule,
            name="lazy_construct",
            author="t",
            head_strategy=strat,
            config=SwitchConfig(starting_phase=Phase.RULE),
        )
        assert strat.calls == 0, "strategy.select() must not run at __init__"

    def test_strategy_not_called_for_classify_at_rule_phase(self):
        # At Phase.RULE the head isn't needed; strategy must stay quiet.
        strat = _CountingStrategy(head=_StaticHead())
        sw = LearnedSwitch(
            rule=_rule,
            name="lazy_rule",
            author="t",
            head_strategy=strat,
            config=SwitchConfig(starting_phase=Phase.RULE),
        )
        sw.classify("anything")
        assert strat.calls == 0

    def test_strategy_called_when_head_first_needed(self):
        strat = _CountingStrategy(head=_StaticHead())
        sw = LearnedSwitch(
            rule=_rule,
            name="lazy_ml_phase",
            author="t",
            head_strategy=strat,
            config=SwitchConfig(starting_phase=Phase.ML_PRIMARY),
        )
        sw.classify("first request")
        assert strat.calls == 1


# ---------------------------------------------------------------------------
# C: ml_head and head_strategy are mutually exclusive
# ---------------------------------------------------------------------------


class TestMutuallyExclusive:
    def test_passing_both_raises(self):
        head = _StaticHead()
        strat = FixedMLHeadStrategy(_StaticHead())
        with pytest.raises(ValueError, match="head_strategy|ml_head"):
            LearnedSwitch(
                rule=_rule,
                name="conflict",
                author="t",
                ml_head=head,
                head_strategy=strat,
                config=SwitchConfig(starting_phase=Phase.RULE),
            )


# ---------------------------------------------------------------------------
# D: Cached after first call
# ---------------------------------------------------------------------------


class TestHeadStrategyCachesPick:
    def test_strategy_called_only_once_across_classifies(self):
        strat = _CountingStrategy(head=_StaticHead())
        sw = LearnedSwitch(
            rule=_rule,
            name="cache_pick",
            author="t",
            head_strategy=strat,
            config=SwitchConfig(starting_phase=Phase.ML_PRIMARY),
        )
        for _ in range(5):
            sw.classify("call")
        assert strat.calls == 1, (
            f"strategy must cache its pick after first use; called {strat.calls} times"
        )
