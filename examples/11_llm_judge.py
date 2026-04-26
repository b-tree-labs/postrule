# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0
"""JudgeSource — single-model judge with the self-judgment guardrail.

Run: `python examples/11_llm_judge.py`

When ground truth is hard to collect but a second, distinct language model
can act as a critic, :class:`JudgeSource` wires one up. The
guardrail: the judge must not be the same language model as the one
producing the classifier's decision. Using the same model as both
classifier and judge is a well-documented bias pattern (G-Eval,
MT-Bench, Chatbot Arena) — the model agrees with its own outputs
even when wrong.

This example uses two local stub language models so it runs without network
or API keys. In production, pass real adapters:

    classifier = OpenAIAdapter(model="gpt-4o-mini")
    judge_model = AnthropicAdapter(model="claude-haiku-4-5")
    judge = JudgeSource(judge_model, require_distinct_from=classifier)

The guardrail fires at construction if both sides resolve to the
same ``(adapter_class, model_string)`` pair.

What the guardrail buys you:

- Bounds a 5–15 pp inflation in perceived accuracy that the
  literature attributes to language model self-judgment (G-Eval, Liu
  et al. 2023; MT-Bench, Zheng et al. 2023).
- Fails at construction, not in production — by the time a
  real classification lands, the judge is already proven
  distinct.

For the situations where this bias actually shows up in a
shipped switch, see ``docs/scenarios.md`` §"Self-judgment
bias guardrail".
"""

from __future__ import annotations

from dendra import (
    LearnedSwitch,
    ModelPrediction,
    Phase,
)
from dendra.verdicts import JudgeSource


class _StubClassifier:
    """Local stand-in for a production language model. Always returns 'bug'."""

    _model = "stub-classifier-v1"

    def classify(self, input, labels):
        return ModelPrediction(label="bug", confidence=0.95)


class _StubJudge:
    """Independent judge — agrees with the classifier on obvious bugs,
    disagrees on ambiguous tickets."""

    _model = "stub-judge-v1"

    def classify(self, input, labels):
        text = str(input).lower()
        # "correct" / "incorrect" / "unknown" — the verdict vocabulary.
        if "crash" in text or "error" in text:
            verdict = "correct"
        elif "maybe" in text or "?" in text:
            verdict = "unknown"
        else:
            verdict = "incorrect"
        return ModelPrediction(label=verdict, confidence=0.8)


def _rule(ticket: dict) -> str:
    title = (ticket.get("title") or "").lower()
    return "bug" if ("crash" in title or "error" in title) else "feature_request"


def main() -> None:
    classifier = _StubClassifier()
    judge_model = _StubJudge()

    # Guardrail: refuses construction if classifier and judge are the
    # same language model. Uncomment to see it raise.
    # shared = _StubClassifier()
    # JudgeSource(shared, require_distinct_from=shared)  # ValueError

    judge = JudgeSource(
        judge_model,
        require_distinct_from=classifier,
    )
    print(f"constructed {judge.source_name}")

    sw = LearnedSwitch(
        rule=_rule,
        model=classifier,
        starting_phase=Phase.MODEL_SHADOW,
        auto_record=False,
        auto_advance=False,
    )

    tickets = [
        {"title": "app crashes on login"},
        {"title": "add dark mode"},
        {"title": "maybe this is a bug?"},
    ]
    for t in tickets:
        result = sw.classify(t)
        verdict = judge.judge(t, result.label)
        # Record via the result-aware path so shadow obs still attach.
        sw.record_verdict(
            input=t,
            label=result.label,
            outcome=verdict.value,
            source=judge.source_name,
            confidence=result.confidence,
            _result_ctx=result,
        )
        print(f"  {t['title']:35s} -> rule={result.label!r:20s} judge={verdict.value}")

    recs = sw.storage.load_records(sw.name)
    print(f"\noutcome log: {len(recs)} rows, sources: {sorted({r.source for r in recs})}")


if __name__ == "__main__":
    main()
