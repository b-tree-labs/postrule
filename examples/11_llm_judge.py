# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0
"""LLMJudgeSource — single-LLM judge with the self-judgment guardrail.

Run: `python examples/11_llm_judge.py`

When ground truth is hard to collect but a second, distinct LLM
can act as a critic, :class:`LLMJudgeSource` wires one up. The
guardrail: the judge must not be the same LLM as the one
producing the classifier's decision. Using the same model as both
classifier and judge is a well-documented bias pattern (G-Eval,
MT-Bench, Chatbot Arena) — the model agrees with its own outputs
even when wrong.

This example uses two local stub LLMs so it runs without network
or API keys. In production, pass real adapters:

    classifier = OpenAIAdapter(model="gpt-4o-mini")
    judge_model = AnthropicAdapter(model="claude-haiku-4-5")
    judge = LLMJudgeSource(judge_model, require_distinct_from=classifier)

The guardrail fires at construction if both sides resolve to the
same ``(adapter_class, model_string)`` pair.
"""

from __future__ import annotations

from dendra import (
    LearnedSwitch,
    ModelPrediction,
    Phase,
    SwitchConfig,
    Verdict,
)
from dendra.verdicts import LLMJudgeSource


class _StubClassifier:
    """Local stand-in for a production LLM. Always returns 'bug'."""

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
    # same LLM. Uncomment to see it raise.
    # shared = _StubClassifier()
    # LLMJudgeSource(shared, require_distinct_from=shared)  # ValueError

    judge = LLMJudgeSource(
        judge_model,
        require_distinct_from=classifier,
    )
    print(f"constructed {judge.source_name}")

    sw = LearnedSwitch(
        rule=_rule,
        name="ticket_triage_with_judge",
        author="@examples:11",
        model=classifier,
        config=SwitchConfig(
            starting_phase=Phase.MODEL_SHADOW,
            auto_record=False,
            auto_advance=False,
        ),
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
        print(
            f"  {t['title']:35s} -> rule={result.label!r:20s} "
            f"judge={verdict.value}"
        )

    recs = sw.storage.load_records(sw.name)
    print(f"\noutcome log: {len(recs)} rows, sources: "
          f"{sorted({r.source for r in recs})}")


if __name__ == "__main__":
    main()
