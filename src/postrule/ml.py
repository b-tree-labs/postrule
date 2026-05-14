# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""ML head protocol + default scikit-learn head.

Phase 3 (ML_SHADOW), Phase 4 (ML_WITH_FALLBACK), and Phase 5 (ML_PRIMARY)
use an :class:`MLHead` to produce classifications. The protocol is
intentionally narrow: ``fit(outcomes)``, ``predict(input, labels)``, and
``model_version()``. Any backend that satisfies the protocol can be
plugged in — sklearn, ONNX-runtime, a remote inference service, or a
fake for tests.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class MLPrediction:
    """What an ML head returns for one input.

    Optional ``tokens_in`` / ``tokens_out`` / ``cost_usd`` fields
    parallel :class:`postrule.models.ModelPrediction` so a remote
    inference-service-backed head can report real per-call spend
    via the close-the-loop benchmark harness. Local sklearn heads
    leave them as ``None`` (no per-call cost).
    """

    label: str
    confidence: float
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None


@runtime_checkable
class MLHead(Protocol):
    """Pluggable ML classifier backend.

    Protocol methods take their primary-input arg positional-only
    (marked with ``/``) so implementations are free to name it
    anything (``ticket``, ``x``, ``request``, …) without tripping
    Protocol name-matching in strict type-checkers.

    **Optional persistence capability.** Heads that *also* implement
    ``state_bytes() -> bytes`` and ``load_state(blob: bytes) -> None``
    survive process restart: when ``persist=True`` on the switch, the
    head's bytes are written to a sidecar file after every advance /
    demote (and on user-invoked ``switch.persist_head()``), and
    rehydrated on construction, so the switch comes up trained. These
    methods are deliberately *not* on the runtime-checkable protocol
    so heads can opt in without breaking the ``isinstance`` check;
    the switch detects the capability via ``hasattr``.
    """

    def fit(self, records: Iterable[Any], /) -> None: ...

    def predict(self, input: Any, labels: Iterable[str], /) -> MLPrediction: ...

    def model_version(self) -> str: ...


# ---------------------------------------------------------------------------
# Default sklearn head — optional, lazy-imported.
# ---------------------------------------------------------------------------


class _MonoclassPredictor:
    """Constant-prediction stand-in for sklearn pipelines on single-class
    training data.

    When the head's training records collapse to a single label (which
    happens on label-sorted training streams before a second class
    appears — e.g. Snips on the HuggingFace ``benayas/snips`` mirror,
    which feeds ~1,842 ``AddToPlaylist`` rows before the next class),
    ``LogisticRegression.fit`` raises ``ValueError`` because LR needs
    >= 2 classes. The pre-fix path bailed out and left the head's
    ``_pipeline`` as ``None``, which caused ``predict`` to return
    ``label=""`` (zero accuracy) while the harness still reported
    ``ml_trained=True``.

    The right semantic: a head that has only seen one label predicts
    that label with full confidence. That matches the rule's modal-
    fallback behavior on the same stream and produces a meaningful,
    interpretable accuracy number (chance on a balanced test set).
    """

    def __init__(self, label: str) -> None:
        import numpy as np

        self._label = label
        # ``classes_`` is consumed via ``list(...)`` in the existing
        # predict path; a 1-element ndarray matches sklearn's contract.
        self.classes_ = np.asarray([label])

    def predict_proba(self, X: Any) -> Any:
        import numpy as np

        # Shape (n_samples, 1); softmax-equivalent for a 1-class output:
        # the single class gets full mass on every row. Returning a
        # numpy array preserves the ``.argmax()`` interface the existing
        # ``predict`` paths rely on.
        n = len(list(X))
        return np.ones((n, 1), dtype=float)

    def decision_function(self, X: Any) -> Any:
        # Mirror sklearn's contract for a 1-class LinearSVC: each input
        # gets a positive margin for the sole class. Returning the same
        # shape sklearn's single-class case would produce keeps
        # TfidfLinearSVCHead.predict happy when it consumes this.
        import numpy as np

        n = len(list(X))
        return np.ones(n, dtype=float)


class SklearnTextHead:
    """Simple TF-IDF + logistic-regression text classifier.

    Used as the zero-config default for text-input switches. Serializes
    inputs with ``repr(input)`` which works well for dict/str/number
    inputs; users with structured inputs should supply their own
    :class:`MLHead`.
    """

    def __init__(self, *, min_outcomes: int = 50) -> None:
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.linear_model import LogisticRegression
            from sklearn.pipeline import Pipeline
        except ImportError as e:
            raise ImportError(
                "SklearnTextHead requires scikit-learn. Install with `pip install postrule[train]`."
            ) from e
        self._Pipeline = Pipeline
        self._Tfidf = TfidfVectorizer
        self._LR = LogisticRegression
        self._pipeline: Any | None = None
        self._min_outcomes = min_outcomes
        self._version = "sklearn-untrained"

    def fit(self, records: Iterable[Any]) -> None:
        records = list(records)
        if len(records) < self._min_outcomes:
            return
        # Use the actual observed label (the `output`) as the training
        # target when the outcome was correct; drop rows we know are wrong
        # so the head doesn't learn to reproduce mistakes.
        X, y = [], []
        for r in records:
            if getattr(r, "outcome", None) != "correct":
                continue
            X.append(serialize_input_for_features(r.input))
            y.append(r.label)
        if not y:
            return
        unique = {*y}
        if len(unique) < 2:
            # Degenerate single-class training data: train a constant
            # predictor that always returns the one observed label.
            # See _MonoclassPredictor docstring for the Snips precedent.
            self._pipeline = _MonoclassPredictor(next(iter(unique)))
            self._version = f"sklearn-{len(records)}-monoclass"
            return
        pipe = self._Pipeline(
            [
                ("tfidf", self._Tfidf(min_df=1)),
                ("clf", self._LR(max_iter=1000)),
            ]
        )
        pipe.fit(X, y)
        self._pipeline = pipe
        self._version = f"sklearn-{len(records)}"

    def predict(self, input: Any, labels: Iterable[str]) -> MLPrediction:
        if self._pipeline is None:
            # Untrained — surface a low-confidence guess so the caller
            # routes to the fallback.
            return MLPrediction(label="", confidence=0.0)
        if isinstance(self._pipeline, _MonoclassPredictor):
            # Single-class training data: predict the one observed label.
            return MLPrediction(label=self._pipeline._label, confidence=1.0)
        probs = self._pipeline.predict_proba([serialize_input_for_features(input)])[0]
        classes = list(self._pipeline.classes_)
        idx = int(probs.argmax())
        return MLPrediction(label=classes[idx], confidence=float(probs[idx]))

    def model_version(self) -> str:
        return self._version

    def state_bytes(self) -> bytes:
        """Pickle the trained pipeline + version. Round-trips via load_state."""
        import pickle

        return pickle.dumps((self._pipeline, self._version), protocol=pickle.HIGHEST_PROTOCOL)

    def load_state(self, blob: bytes) -> None:
        """Restore a previously-pickled pipeline + version."""
        import pickle

        pipeline, version = pickle.loads(blob)
        self._pipeline = pipeline
        self._version = version


def _build_tfidf_pipeline(estimator: Any, *, Pipeline: Any, Tfidf: Any) -> Any:
    """Shared TF-IDF + classifier pipeline factory."""
    return Pipeline(
        [
            ("tfidf", Tfidf(min_df=1)),
            ("clf", estimator),
        ]
    )


def _fit_tfidf_head(self: Any, records: Iterable[Any]) -> None:
    """Shared fit-from-correctly-classified-records routine.

    Used by every TF-IDF-based head. Subclasses override
    ``_build_classifier()`` to plug in a different estimator.
    """
    records = list(records)
    if len(records) < self._min_outcomes:
        return
    X, y = [], []
    for r in records:
        if getattr(r, "outcome", None) != "correct":
            continue
        X.append(serialize_input_for_features(r.input))
        y.append(r.label)
    if not y:
        return
    unique = {*y}
    if len(unique) < 2:
        # Single-class training data: train a constant predictor that
        # always returns the observed label. See _MonoclassPredictor.
        self._pipeline = _MonoclassPredictor(next(iter(unique)))
        self._version = f"{type(self).__name__}-{len(records)}-monoclass"
        return
    pipe = _build_tfidf_pipeline(
        self._build_classifier(),
        Pipeline=self._Pipeline,
        Tfidf=self._Tfidf,
    )
    pipe.fit(X, y)
    self._pipeline = pipe
    self._version = f"{type(self).__name__}-{len(records)}"


def _predict_tfidf_head(self: Any, input: Any, labels: Iterable[str]) -> MLPrediction:
    if self._pipeline is None:
        return MLPrediction(label="", confidence=0.0)
    if isinstance(self._pipeline, _MonoclassPredictor):
        # Constant-class predictor: always returns the one observed label.
        return MLPrediction(label=self._pipeline._label, confidence=1.0)
    probs = self._pipeline.predict_proba([serialize_input_for_features(input)])[0]
    classes = list(self._pipeline.classes_)
    idx = int(probs.argmax())
    return MLPrediction(label=classes[idx], confidence=float(probs[idx]))


class TfidfHeadBase:
    """Public extension base for TF-IDF + sklearn-classifier heads.

    Subclass and override :meth:`_build_classifier` to ship your own
    text-classification head with a different estimator. The shared
    pipeline (TF-IDF feature extraction, fit-from-correct-records,
    predict-with-confidence, model_version, state_bytes, load_state)
    is inherited free.

    Example::

        from sklearn.linear_model import LogisticRegression
        from postrule import TfidfHeadBase, register_ml_head

        class CustomLogRegHead(TfidfHeadBase):
            def _build_classifier(self):
                return LogisticRegression(max_iter=500, C=0.1, penalty="l1")

        register_ml_head("custom_logreg", lambda: CustomLogRegHead())

    Once registered, the head is addressable by name via
    :func:`make_ml_head` and can be returned by an
    :class:`MLHeadStrategy` without importing the class.
    """

    def __init__(self, *, min_outcomes: int = 50) -> None:
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.pipeline import Pipeline
        except ImportError as e:
            raise ImportError(
                f"{type(self).__name__} requires scikit-learn. "
                f"Install with `pip install postrule[train]`."
            ) from e
        self._Pipeline = Pipeline
        self._Tfidf = TfidfVectorizer
        self._pipeline: Any | None = None
        self._min_outcomes = min_outcomes
        self._version = f"{type(self).__name__}-untrained"

    def _build_classifier(self) -> Any:
        raise NotImplementedError

    def fit(self, records: Iterable[Any]) -> None:
        _fit_tfidf_head(self, records)

    def predict(self, input: Any, labels: Iterable[str]) -> MLPrediction:
        return _predict_tfidf_head(self, input, labels)

    def model_version(self) -> str:
        return self._version

    def state_bytes(self) -> bytes:
        import pickle

        return pickle.dumps((self._pipeline, self._version), protocol=pickle.HIGHEST_PROTOCOL)

    def load_state(self, blob: bytes) -> None:
        import pickle

        pipeline, version = pickle.loads(blob)
        self._pipeline = pipeline
        self._version = version


class TfidfLinearSVCHead(TfidfHeadBase):
    """Linear SVM head with softmax-normalized decision-function scores.

    Avoids CalibratedClassifierCV (which requires ≥cv samples per
    class and breaks on rare-class benchmarks like ATIS). Instead,
    convert decision_function output to a probability-like
    confidence via softmax (multi-class) or sigmoid (binary).
    """

    def _build_classifier(self) -> Any:
        from sklearn.svm import LinearSVC

        return LinearSVC(max_iter=2000)

    def predict(self, input: Any, labels: Iterable[str]) -> MLPrediction:
        if self._pipeline is None:
            return MLPrediction(label="", confidence=0.0)
        if isinstance(self._pipeline, _MonoclassPredictor):
            return MLPrediction(label=self._pipeline._label, confidence=1.0)
        import numpy as np

        scores = self._pipeline.decision_function([serialize_input_for_features(input)])[0]
        scores = np.atleast_1d(scores)
        classes = list(self._pipeline.classes_)
        if scores.size == 1:
            # Binary case: scalar decision -> sigmoid.
            p_pos = float(1.0 / (1.0 + np.exp(-scores[0])))
            if p_pos >= 0.5:
                return MLPrediction(label=classes[1], confidence=p_pos)
            return MLPrediction(label=classes[0], confidence=1.0 - p_pos)
        # Multi-class: softmax over decision scores.
        m = scores.max()
        e = np.exp(scores - m)
        probs = e / e.sum()
        idx = int(probs.argmax())
        return MLPrediction(label=classes[idx], confidence=float(probs[idx]))


class TfidfMultinomialNBHead(TfidfHeadBase):
    """Multinomial Naive Bayes head — classic strong text-classification baseline."""

    def _build_classifier(self) -> Any:
        from sklearn.naive_bayes import MultinomialNB

        return MultinomialNB()


class TfidfGradientBoostingHead(TfidfHeadBase):
    """Gradient-boosted trees head. Slower to train; useful as a non-linear
    contrast to the linear estimators."""

    def _build_classifier(self) -> Any:
        from sklearn.ensemble import GradientBoostingClassifier

        return GradientBoostingClassifier(n_estimators=50, max_depth=3)


class ImagePixelLogRegHead:
    """Logistic-regression head for image classification on flat pixels.

    Used by the CIFAR-10 v1.0 image bench (paper §5.8). Each input
    is a numpy uint8 array of shape (H, W, 3); the head normalizes
    to [0, 1], flattens, and fits a multinomial logistic regression.
    Companion to the TF-IDF text heads — same protocol surface,
    different feature path. Deliberately simple: pretrained
    embeddings (CLIP, ViT) would raise the ML ceiling but are
    deferred to v1.x to avoid a torch dependency on the v1.0 install
    path.
    """

    def __init__(self, *, min_outcomes: int = 50) -> None:
        try:
            from sklearn.linear_model import LogisticRegression
        except ImportError as e:
            raise ImportError(
                "ImagePixelLogRegHead requires scikit-learn. "
                "Install with `pip install postrule[train]`."
            ) from e
        self._LR = LogisticRegression
        self._classifier: Any | None = None
        self._classes: list[str] = []
        self._min_outcomes = min_outcomes
        self._version = "ImagePixelLogRegHead-untrained"

    def fit(self, records: Iterable[Any]) -> None:
        import numpy as np

        records = list(records)
        if len(records) < self._min_outcomes:
            return
        X, y = [], []
        for r in records:
            if getattr(r, "outcome", None) != "correct":
                continue
            img = r.input
            if not isinstance(img, np.ndarray):
                continue
            X.append((img.astype(np.float32) / 255.0).reshape(-1))
            y.append(r.label)
        if len({*y}) < 2:
            return
        Xa = np.stack(X)
        ya = np.asarray(y)
        clf = self._LR(max_iter=1000)
        clf.fit(Xa, ya)
        self._classifier = clf
        self._classes = list(clf.classes_)
        self._version = f"ImagePixelLogRegHead-{len(records)}"

    def predict(self, input: Any, labels: Iterable[str]) -> MLPrediction:
        import numpy as np

        if self._classifier is None or not isinstance(input, np.ndarray):
            return MLPrediction(label="", confidence=0.0)
        x = (input.astype(np.float32) / 255.0).reshape(1, -1)
        probs = self._classifier.predict_proba(x)[0]
        idx = int(probs.argmax())
        return MLPrediction(label=self._classes[idx], confidence=float(probs[idx]))

    def model_version(self) -> str:
        return self._version

    def state_bytes(self) -> bytes:
        import pickle

        return pickle.dumps(
            (self._classifier, self._classes, self._version),
            protocol=pickle.HIGHEST_PROTOCOL,
        )

    def load_state(self, blob: bytes) -> None:
        import pickle

        clf, classes, version = pickle.loads(blob)
        self._classifier = clf
        self._classes = list(classes) if classes is not None else []
        self._version = version


def serialize_input_for_features(value: Any) -> str:
    """Turn an arbitrary Postrule classifier input into a feature string.

    Rules, in order (most→least specific):

    1. ``str`` — passed through.
    2. ``dict`` — each ``k: v`` pair joined, string values rendered raw,
       nested dicts recursed, other types repr'd. Preserves key names so
       TF-IDF tokenization can weight ``title:`` hits vs ``body:`` hits.
    3. ``list`` / ``tuple`` — elements joined by spaces after recursing.
    4. ``None`` — empty string.
    5. Anything else — ``repr(value)`` as a universal fallback.

    This is the module's *auto feature extraction* for text-oriented ML
    heads. The philosophy: do something sensible by default so adopters
    who pass "realistic" classifier inputs (strings, ticket dicts,
    trimmed records) get decent features without writing a pipeline.
    Advanced users plug a custom :class:`MLHead` and take over entirely.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts = []
        for k, v in value.items():
            parts.append(f"{k}: {serialize_input_for_features(v)}")
        return " | ".join(parts)
    if isinstance(value, (list, tuple)):
        return " ".join(serialize_input_for_features(v) for v in value)
    if isinstance(value, (int, float, bool)):
        return repr(value)
    return repr(value)


# ---------------------------------------------------------------------------
# Factory / registry — name-addressable MLHeads for pluggable strategies.
# ---------------------------------------------------------------------------


@runtime_checkable
class MLHeadFactory(Protocol):
    """Zero-arg callable that returns a fresh MLHead instance.

    Used by :func:`register_ml_head` to associate a string name with
    a head builder. Strategies and configuration that refers to
    heads by name (e.g. JSON/YAML config files, CLI flags, paper
    autoresearch tables) consume factories rather than class names.
    """

    def __call__(self) -> MLHead: ...


_HEAD_REGISTRY: dict[str, MLHeadFactory] = {}


def register_ml_head(name: str, factory: MLHeadFactory) -> None:
    """Register a name → factory mapping for an MLHead.

    Re-registering an existing name raises ``ValueError`` so two
    plugins can't silently shadow each other; if you want to swap a
    head, ``_HEAD_REGISTRY.pop(name)`` first or pick a unique name.
    """
    if not name:
        raise ValueError("MLHead name cannot be empty")
    if not callable(factory):
        raise TypeError("factory must be callable returning an MLHead")
    if name in _HEAD_REGISTRY:
        raise ValueError(
            f"MLHead {name!r} is already registered; pick a unique name "
            f"or pop the existing entry first"
        )
    _HEAD_REGISTRY[name] = factory


def make_ml_head(name: str) -> MLHead:
    """Instantiate the registered head with the given name.

    Raises ``ValueError`` with the available names if ``name`` is
    not registered.
    """
    if name not in _HEAD_REGISTRY:
        raise ValueError(f"unknown MLHead {name!r}; registered: {sorted(_HEAD_REGISTRY)}")
    return _HEAD_REGISTRY[name]()


def available_ml_heads() -> list[str]:
    """Return the list of currently-registered head names."""
    return sorted(_HEAD_REGISTRY)


# Auto-register the built-in heads. Plugins extending Postrule register
# their own heads at import time via ``register_ml_head``.
register_ml_head("tfidf_logreg", lambda: SklearnTextHead())
register_ml_head("tfidf_linearsvc", lambda: TfidfLinearSVCHead())
register_ml_head("tfidf_multinomial_nb", lambda: TfidfMultinomialNBHead())
register_ml_head("tfidf_gradient_boosting", lambda: TfidfGradientBoostingHead())
register_ml_head("image_pixel_logreg", lambda: ImagePixelLogRegHead())


__all__ = [
    "ImagePixelLogRegHead",
    "MLHead",
    "MLHeadFactory",
    "MLPrediction",
    "SklearnTextHead",
    "TfidfGradientBoostingHead",
    "TfidfHeadBase",
    "TfidfLinearSVCHead",
    "TfidfMultinomialNBHead",
    "available_ml_heads",
    "make_ml_head",
    "register_ml_head",
    "serialize_input_for_features",
]
