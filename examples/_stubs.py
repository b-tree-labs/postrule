# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0
"""Demo-only fixtures for the examples gallery.

> **Reading the examples?** Skip this file. Everything in here is a
> stand-in so the example runs offline without API keys. The values
> these fakes return are NOT load-bearing for any example's
> teaching point.

The purpose: every example in `examples/01_*.py` through
`examples/20_*.py` is self-contained, runs without network, and
demonstrates exactly one Dendra concept. To keep that promise we
need stand-ins for the real language-model adapters / ML heads / verdict
sources — and we want those stand-ins to be visually obvious so
a reader doesn't waste cognitive cycles tracing whether the
fixture is "real" or "demo only."

The contract: every class in this module is named with a `Fake`
prefix, has a clear docstring, and includes a "production swap-
in" line showing what to replace it with.

Examples import from here as a sibling module:

    from _stubs import FakeJudgeLM
    # ... use FakeJudgeLM wherever a real language-model adapter would go.

Python's automatic addition of the script's directory to
`sys.path[0]` makes the bare `from _stubs import ...` work when
running `python examples/20_*.py` from the repo root. No package
structure required.
"""

from __future__ import annotations

from typing import Any

from dendra import MLPrediction, ModelPrediction, Verdict


# ---------------------------------------------------------------------------
# language-model adapters — fake "ModelClassifier" implementations
# ---------------------------------------------------------------------------


class FakeJudgeLM:
    """Demo fixture — pretends to be a model judge for offline runs.

    **Production swap-in:**

    - ``JudgeSource(OllamaAdapter(model="llama3.2:3b"))`` — local, zero cost
    - ``JudgeSource(OpenAIAdapter(model="gpt-4o-mini"))`` — cloud, fastest
    - ``default_verifier()`` — auto-detects whichever you have

    The classify() method is given a rendered prompt (string)
    that contains the original input + the classifier's label.
    It returns ``correct`` / ``incorrect`` / ``unknown`` so the
    surrounding JudgeSource can map to a Verdict.

    Toy logic: agree (return "correct") on prompts that mention
    "crash" or "how do i"; disagree otherwise. The specific
    values are NOT load-bearing for the example's teaching point —
    they're just deterministic so the demo output is reproducible.
    """

    _model = "fake-llm-judge-demo"

    def classify(self, input: Any, labels: Any) -> ModelPrediction:
        text = str(input).lower()
        if "crash" in text or "how do i" in text:
            return ModelPrediction(label="correct", confidence=0.92)
        return ModelPrediction(label="incorrect", confidence=0.85)


class FakeLMClassifier:
    """Demo fixture — pretends to be a language model classifier for offline runs.

    **Production swap-in:**

    - ``OpenAIAdapter(model="gpt-4o-mini")``
    - ``AnthropicAdapter(model="claude-haiku-4-5")``
    - ``OllamaAdapter(model="llama3.2:3b")``

    Used in examples that show MODEL_SHADOW or MODEL_PRIMARY
    phases. Returns a deterministic prediction matching the
    teaching point of whatever example imports it.
    """

    def __init__(
        self,
        *,
        label: str = "bug",
        confidence: float = 0.95,
        model: str = "fake-classifier-demo",
    ) -> None:
        self._label = label
        self._confidence = confidence
        self._model = model

    def classify(self, input: Any, labels: Any) -> ModelPrediction:
        return ModelPrediction(label=self._label, confidence=self._confidence)


# ---------------------------------------------------------------------------
# ML heads — fake MLHead implementations
# ---------------------------------------------------------------------------


class FakeMLHead:
    """Demo fixture — pretends to be a trained ML head for offline runs.

    **Production swap-in:**

    - ``SklearnTextHead(min_outcomes=100)`` — TF-IDF + logistic regression
    - Custom :class:`MLHead`-conforming object wrapping your
      transformer / fine-tuned model / ONNX runtime

    Returns a deterministic prediction; ``fit()`` is a no-op
    (the fake doesn't learn — it's a placeholder for the
    structural teaching point of whatever example imports it).
    """

    def __init__(
        self,
        *,
        label: str = "bug",
        confidence: float = 0.93,
        version: str = "fake-ml-head-1.0",
    ) -> None:
        self._label = label
        self._confidence = confidence
        self._version = version

    def fit(self, _records: Any) -> None:
        """No-op — a real ML head trains here."""

    def predict(self, _input: Any, _labels: Any = None) -> MLPrediction:
        return MLPrediction(label=self._label, confidence=self._confidence)

    def model_version(self) -> str:
        return self._version


class FakeFlakyMLHead:
    """Demo fixture — pretends to be an ML head that can be forced to fail.

    Used in examples demonstrating the circuit-breaker behavior
    (see ``06_ml_primary.py``). Toggle ``raise_on_next`` to
    True and the next ``predict()`` raises a RuntimeError.

    **Production analog:** any real ML head that occasionally
    errors out (network blip, OOM, model-server restart). The
    breaker semantics are the same regardless of the failure
    cause.
    """

    def __init__(self) -> None:
        self.raise_on_next: bool = False

    def fit(self, _records: Any) -> None:
        """No-op."""

    def predict(self, _input: Any, _labels: Any = None) -> MLPrediction:
        if self.raise_on_next:
            raise RuntimeError("model server returned 503")
        return MLPrediction(label="question", confidence=0.95)

    def model_version(self) -> str:
        return "fake-flaky-ml-head-0.1"


# ---------------------------------------------------------------------------
# Verdict sources — fake VerdictSource implementations beyond the model judges
# above (the JudgeSource family already wraps FakeJudgeLM cleanly)
# ---------------------------------------------------------------------------


class FakeOracle:
    """Demo fixture — pretends to be a ground-truth oracle.

    **Production swap-in:**

    - A labeled validation-set lookup
    - A downstream-signal aggregator (with the time-delay wrapper
      to wait for the signal)
    - An ``JudgeCommittee`` for high-quality consensus

    Returns whatever ground-truth label the example needs to
    demonstrate its concept.
    """

    def __init__(self, *, true_label: str = "bug") -> None:
        self._true_label = true_label

    def __call__(self, _input: Any) -> str:
        return self._true_label
