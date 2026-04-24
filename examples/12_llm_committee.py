# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0
"""LLMCommitteeSource — multi-LLM committee aggregation.

Run: `python examples/12_llm_committee.py`

A committee of distinct LLMs judges each classification. Three
aggregation modes:

- ``majority``: the plurality wins; ties go to UNKNOWN.
- ``unanimous``: all judges must agree on a non-UNKNOWN verdict;
  any disagreement → UNKNOWN. Useful when false positives are
  expensive.
- ``confidence_weighted``: reserved for future extension; for v1
  this falls through to majority semantics so callers can pin the
  enum name today and upgrade later without code changes.

The same self-judgment guardrail as :class:`LLMJudgeSource`
applies: none of the committee members may be the same LLM as
the classifier. Construction fails loudly if they are.

This example runs locally with stubs; swap in real adapters for
production:

    judges = [
        OpenAIAdapter(model="gpt-4o-mini"),
        AnthropicAdapter(model="claude-haiku-4-5"),
        OllamaAdapter(model="llama3.2:1b"),
    ]
"""

from __future__ import annotations

from dendra import ModelPrediction, Verdict
from dendra.verdicts import LLMCommitteeSource


class _StubJudge:
    """Judge that always returns a fixed verdict label."""

    def __init__(self, model: str, verdict: str) -> None:
        self._model = model
        self._verdict = verdict

    def classify(self, input, labels):
        return ModelPrediction(label=self._verdict, confidence=0.9)


def main() -> None:
    # --- Majority mode: 2-of-3 agreement wins --------------------------------
    print("Pattern 1: majority\n")
    majority_judges = [
        _StubJudge("gpt-4o-mini", "correct"),
        _StubJudge("claude-haiku-4-5", "correct"),
        _StubJudge("llama3.2:1b", "incorrect"),  # dissent
    ]
    committee = LLMCommitteeSource(majority_judges, mode="majority")
    print(f"  {committee.source_name}")
    print(f"  verdict: {committee.judge('input', 'bug').value}")

    # --- Unanimous mode: any disagreement → UNKNOWN --------------------------
    print("\nPattern 2: unanimous (high-stakes path)\n")
    unan_judges = [
        _StubJudge("gpt-4o-mini", "correct"),
        _StubJudge("claude-haiku-4-5", "incorrect"),  # blocks
        _StubJudge("llama3.2:1b", "correct"),
    ]
    strict = LLMCommitteeSource(unan_judges, mode="unanimous")
    print(f"  {strict.source_name}")
    v = strict.judge("input", "bug")
    print(f"  verdict: {v.value}  # one dissent → UNKNOWN, not CORRECT")

    # Flip all three to correct → unanimous fires.
    agree_all = LLMCommitteeSource(
        [
            _StubJudge("gpt-4o-mini", "correct"),
            _StubJudge("claude-haiku-4-5", "correct"),
            _StubJudge("llama3.2:1b", "correct"),
        ],
        mode="unanimous",
    )
    print(f"  all-agree verdict: {agree_all.judge('input', 'bug').value}")

    # --- Guardrail: committee clone of the classifier refused ---------------
    print("\nPattern 3: guardrail\n")
    try:
        classifier = _StubJudge("gpt-4o-mini", "correct")
        LLMCommitteeSource(
            [
                _StubJudge("claude-haiku-4-5", "correct"),
                _StubJudge("gpt-4o-mini", "correct"),  # same as classifier
            ],
            require_distinct_from=classifier,
        )
    except ValueError as e:
        print(f"  refused construction: {type(e).__name__}")
        print(f"  reason (truncated): {str(e)[:80]}...")


if __name__ == "__main__":
    main()
