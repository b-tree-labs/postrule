# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0
"""Cold-start: bootstrap from MODEL_PRIMARY, then graduate to ML.

Run: `python examples/07_llm_as_teacher.py`

Cold-start: new product, zero labeled data, no hand-written rule
yet. The LLM-as-teacher pattern:

1. Start at ``Phase.MODEL_PRIMARY`` — the LLM decides. Every call
   logs ``(input, llm_label)``: the LLM is labeling your data for
   you, in production.
2. Call ``switch.advance()`` periodically. The default
   :class:`McNemarGate` reads the paired-prediction log; when the
   target phase is statistically better (p < 0.01 on ≥200 paired
   samples), the switch graduates itself. No manual phase mutation.
3. Train a local ML head on the accumulated LLM labels; the ML
   head carries subsequent graduations toward ML_WITH_FALLBACK.

Graduation is evidence-gated, not operator-gated. Custom gates
(``ManualGate`` for always-operator-approval, composite gates
with extra thresholds) are swappable via the ``gate=`` kwarg.

Uses a stub LLM so the script runs without API credentials.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from dendra import LearnedSwitch, MLPrediction, ModelPrediction, Phase, Verdict


def triage_rule(ticket: dict) -> str:
    """Eventual safety floor — written LATER once the LLM has taught
    us the patterns. Defined here so the code runs."""
    heading = (ticket.get("title") or "").lower()
    if "crash" in heading or "error" in heading:
        return "bug"
    return "feature_request"


class StubLLM:
    """Deterministic stand-in for a real LLM adapter."""

    def classify(self, ticket: Any, _labels: Iterable[str]) -> ModelPrediction:
        """Return a deterministic prediction for the given ticket."""
        heading = (ticket.get("title") or "").lower()
        if any(kw in heading for kw in ("crash", "error", "broken", "hang")):
            return ModelPrediction(label="bug", confidence=0.94)
        if any(kw in heading for kw in ("add", "support for", "option to", "feature")):
            return ModelPrediction(label="feature_request", confidence=0.90)
        if heading.endswith("?") or heading.startswith("how ") or heading.startswith("can i"):
            return ModelPrediction(label="question", confidence=0.88)
        return ModelPrediction(label="feature_request", confidence=0.72)


class LocalMLHead:
    """Trained on the LLM's accumulated labels. Production would be
    scikit-learn TF-IDF+LR, a sentence-transformer probe, or a small
    ONNX classifier — here we ship a toy for determinism."""

    def __init__(self) -> None:
        self._label_counts: dict[str, int] = {}

    def fit(self, records):
        """Count the most-common LLM-assigned labels from the log."""
        self._label_counts = {}
        for r in records:
            if r.model_output is None:
                continue
            self._label_counts[r.model_output] = self._label_counts.get(r.model_output, 0) + 1

    def predict(self, ticket, _labels=None) -> MLPrediction:
        """Predict a label for the ticket; fall back to the most-common
        observed label for unfamiliar inputs."""
        heading = (ticket.get("title") or "").lower()
        if any(kw in heading for kw in ("crash", "error", "broken")):
            return MLPrediction(label="bug", confidence=0.91)
        if any(kw in heading for kw in ("add", "support")):
            return MLPrediction(label="feature_request", confidence=0.85)
        if self._label_counts:
            # `key=dict.__getitem__` returns a guaranteed int (we're
            # iterating the dict's own keys); `dict.get` returns
            # int | None, which Pylance rejects under strict mode.
            most_common = max(self._label_counts, key=self._label_counts.__getitem__)
            return MLPrediction(label=most_common, confidence=0.55)
        return MLPrediction(label="question", confidence=0.50)

    def model_version(self) -> str:
        """Version string surfaced in ``SwitchStatus.model_version``."""
        return "local-stub-1.0"


if __name__ == "__main__":
    # ------------------------------------------------------------
    # Step 1: Bootstrap at MODEL_PRIMARY — LLM decides, logs labels.
    # ------------------------------------------------------------
    print("--- Bootstrap: starting at MODEL_PRIMARY with no ML head ---\n")

    # Production: pass ``persist=True`` so the cold-start log
    # (weeks of LLM labels) survives process restart.
    switch = LearnedSwitch(
        name="triage-bootstrap",
        rule=triage_rule,
        model=StubLLM(),
        starting_phase=Phase.MODEL_PRIMARY,
    )

    early_tickets = [
        ("app crashes on login", "bug"),
        ("how do I reset my password?", "question"),
        ("error in checkout flow", "bug"),
        ("add dark mode option", "feature_request"),
        ("can i download my data?", "question"),
        ("payment page is broken", "bug"),
        ("support for markdown in comments", "feature_request"),
    ]
    for subject, ground_truth in early_tickets:
        payload = {"title": subject}
        result = switch.classify(payload)
        verdict = Verdict.CORRECT if result.label == ground_truth else Verdict.INCORRECT
        switch.record_verdict(input=payload, label=result.label, outcome=verdict.value)

    bootstrap_log = switch.storage.load_records("triage-bootstrap")
    correct = sum(1 for r in bootstrap_log if r.outcome == Verdict.CORRECT.value)
    print(
        f"  LLM decided {len(bootstrap_log)} tickets; "
        f"{correct} correct ({correct/len(bootstrap_log):.0%})"
    )
    print("  Every ticket is now (input, llm_label) training data.\n")

    # ------------------------------------------------------------
    # Step 2: Train ML head on the LLM's labels, promote phase.
    # ------------------------------------------------------------
    print("--- Graduate (operator-triggered): train ML + promote phase ---\n")

    ml_head = LocalMLHead()
    ml_head.fit(bootstrap_log)

    # Skipping ML_SHADOW for brevity; safety-conscious deployments
    # would run ML in shadow for another evidence window first.
    switch2 = LearnedSwitch(
        name="triage-graduated",
        rule=triage_rule,
        model=StubLLM(),  # kept warm for retraining / fallback
        ml_head=ml_head,
        starting_phase=Phase.ML_WITH_FALLBACK,
    )

    new_tickets = [
        {"title": "app crashed during onboarding"},
        {"title": "how to integrate with Slack?"},
        {"title": "add support for CSV export"},
    ]
    for sample in new_tickets:
        result = switch2.classify(sample)
        print(f"  {sample['title']:40s}  → {result.label:18s}  source={result.source}")

    print("\n(The LLM taught the ML head by labeling production traffic.")
    print(" The rule was never on the hot path — but it's still there as")
    print(" the safety floor.)")
