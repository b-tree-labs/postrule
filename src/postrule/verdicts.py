# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Verdict sources — pluggable "where does truth come from" primitives.

A :class:`VerdictSource` decides whether a classification decision
matched ground truth. Verdicts feed the outcome log, drive gate
graduation math, and populate the audit trail.

Postrule ships with three built-in sources:

- :class:`CallableVerdictSource` — wraps any
  ``(input, label) -> Verdict`` callable. The escape hatch for
  bespoke truth oracles (downstream signals, business rules,
  reviewer decisions already on hand).
- :class:`JudgeSource` — single-model judge. Prompts the model
  to assess whether a label is correct for an input. Guards
  against the self-judgment bias pattern called out in G-Eval,
  MT-Bench, and Arena evaluation literature: running the SAME
  language model as classifier and as judge is a known failure mode.
- :class:`JudgeCommittee` — multi-model committee. Combines
  per-model verdicts via majority vote, unanimous agreement, or
  confidence-weighted aggregation.

Custom sources: implement the :class:`VerdictSource` protocol
(``judge(input, label) -> Verdict``) on any object. The protocol
is :func:`runtime_checkable` so ``isinstance(obj, VerdictSource)``
works without inheritance.

Every verdict is stamped with a ``source`` string
(``"judge:<model>"`` / ``"committee:<ids>"`` /
``"callable:<name>"``) so audit-chain filters can separate
self-reported machine verdicts from human-reviewed truth.

See ``docs/verdict-sources.md`` (roadmap) for the full matrix
of when-to-use-which, and ``examples/11_llm_judge.py`` /
``examples/12_llm_committee.py`` for wired end-to-end demos.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol, runtime_checkable

from postrule.core import Verdict
from postrule.models import ModelClassifier

# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class VerdictSource(Protocol):
    """A pluggable source of truth for classification verdicts.

    Implementations return one of the three :class:`Verdict`
    values (``CORRECT`` / ``INCORRECT`` / ``UNKNOWN``) given the
    original input and the decision label the switch returned.

    ``source_name`` is a stable string used in audit-trail
    stamping. Two :class:`VerdictSource` instances with the same
    ``source_name`` must be interchangeable in their judge
    semantics.

    Sources that expose an ``ajudge(input, label)`` coroutine
    can be passed to :meth:`LearnedSwitch.abulk_record_verdicts_from_source`
    for native-async pipelines. When ``ajudge`` is absent, the
    async bulk path wraps :meth:`judge` via ``asyncio.to_thread``.
    """

    source_name: str

    def judge(self, input: Any, label: Any, /) -> Verdict: ...


# ---------------------------------------------------------------------------
# CallableVerdictSource — escape hatch
# ---------------------------------------------------------------------------


class CallableVerdictSource:
    """Wrap any ``(input, label) -> Verdict`` callable as a VerdictSource.

    Use when the truth signal isn't a language model — a downstream service's
    return code, a database lookup, a business-rule function,
    a reviewer's notebook output pre-computed as a dict lookup.
    """

    def __init__(
        self,
        fn: Callable[[Any, Any], Verdict],
        *,
        name: str = "callable",
    ) -> None:
        if not callable(fn):
            raise TypeError("fn must be callable")
        if not name:
            raise ValueError("name cannot be empty")
        self._fn = fn
        self.source_name = f"callable:{name}"

    def judge(self, input: Any, label: Any, /) -> Verdict:
        result = self._fn(input, label)
        if not isinstance(result, Verdict):
            raise TypeError(
                f"callable verdict source {self.source_name!r} must return a "
                f"Verdict; got {type(result).__name__}"
            )
        return result


# ---------------------------------------------------------------------------
# JudgeSource — single-model judge with bias guardrails
# ---------------------------------------------------------------------------


_JUDGE_LABELS = ["correct", "incorrect", "unknown"]


def _is_model_like(obj: Any) -> bool:
    """Accept sync (:class:`ModelClassifier`) *or* async adapters.

    Async adapters expose ``aclassify`` instead of ``classify``;
    both shapes satisfy "something that maps (input, labels) to a
    ModelPrediction." JudgeSource / JudgeCommittee accept
    either and dispatch to whichever is present.
    """
    return callable(getattr(obj, "classify", None)) or callable(getattr(obj, "aclassify", None))


def _identify_model(llm: Any) -> tuple[str, str]:
    """Extract a ``(class_name, model_string)`` pair for identity checks."""
    cls = type(llm).__name__
    model = str(getattr(llm, "_model", None) or getattr(llm, "model", None) or "")
    return cls, model


def _same_model(a: ModelClassifier, b: ModelClassifier) -> bool:
    """Best-effort identity check.

    True when ``a`` and ``b`` are literally the same object, or when
    they expose the same ``(class_name, model_string)`` pair —
    which catches "two separate OpenAIAdapter(model='gpt-4o-mini')
    instances."
    """
    if a is b:
        return True
    return _identify_model(a) == _identify_model(b)


class JudgeSource:
    """Single-model judge.

    Prompts ``judge_model`` to evaluate whether ``label`` is the
    correct classification for ``input``. Returns one of
    :class:`Verdict` based on the judge's response.

    Also exposes an async peer :meth:`ajudge` when ``judge_model``
    carries an ``aclassify`` coroutine (i.e., one of the
    ``...AsyncAdapter`` siblings from :mod:`postrule.models`). The
    async path skips the ``asyncio.to_thread`` hop — native async
    all the way through.

    Bias guardrail
    --------------
    Using the same language model as both classifier and judge is a
    well-documented anti-pattern — the same model tends to agree
    with its own outputs even when wrong. When
    ``guard_against_same_llm=True`` (the default), constructing
    a judge alongside a ``require_distinct_from=`` classifier
    that points at the same provider/model raises
    ``ValueError`` immediately. Pass
    ``guard_against_same_llm=False`` only if you explicitly
    accept the self-judgment bias risk and have your own mitigation.

    References: G-Eval (NAACL 2023), MT-Bench (NeurIPS 2023),
    Chatbot Arena (ICML 2024) — all report meaningful bias when
    the same model judges its own outputs.
    """

    def __init__(
        self,
        judge_model: ModelClassifier,
        *,
        require_distinct_from: ModelClassifier | None = None,
        guard_against_same_llm: bool = True,
        prompt_template: str | None = None,
    ) -> None:
        if not _is_model_like(judge_model):
            raise TypeError(
                "judge_model must expose classify(input, labels) -> "
                "ModelPrediction (sync) or aclassify(input, labels) "
                "-> ModelPrediction (async)."
            )
        if (
            guard_against_same_llm
            and require_distinct_from is not None
            and _same_model(judge_model, require_distinct_from)
        ):
            cls, model = _identify_model(judge_model)
            raise ValueError(
                f"refusing to construct JudgeSource: judge_model and "
                f"require_distinct_from resolve to the same language model "
                f"({cls} / model={model!r}). Using the same language model as "
                f"classifier and judge biases verdicts toward the "
                f"classifier's own errors — see G-Eval / MT-Bench / "
                f"Arena literature. Pass a distinct model, or set "
                f"guard_against_same_llm=False if you explicitly "
                f"accept the bias risk."
            )
        self._judge = judge_model
        cls, model = _identify_model(judge_model)
        tag = model or cls
        self.source_name = f"judge:{tag}"
        self._prompt_template = prompt_template or _DEFAULT_JUDGE_PROMPT

    def judge(self, input: Any, label: Any, /) -> Verdict:
        prompt = self._prompt_template.format(input=input, label=label)
        try:
            classify = getattr(self._judge, "classify", None)
            if classify is not None:
                pred = classify(prompt, _JUDGE_LABELS)
            else:
                # Async-only judge called from sync context — run the
                # coroutine on a fresh event loop. Slower than the
                # async path but keeps the sync API usable.
                import asyncio

                pred = asyncio.run(
                    self._judge.aclassify(prompt, _JUDGE_LABELS),
                )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            # A judge-side outage must not break the caller's audit
            # loop. Surface UNKNOWN so the record is recoverable but
            # the downstream gate math correctly ignores unverdicted
            # rows.
            return Verdict.UNKNOWN
        return _parse_judge_label(pred.label)

    async def ajudge(self, input: Any, label: Any, /) -> Verdict:
        """Async peer of :meth:`judge`.

        Uses ``judge_model.aclassify`` when available (true-async
        path; no thread hop). Falls back to wrapping :meth:`judge`
        via ``asyncio.to_thread`` when the judge is a sync adapter.
        """
        import asyncio

        aclassify = getattr(self._judge, "aclassify", None)
        if aclassify is None:
            return await asyncio.to_thread(self.judge, input, label)
        prompt = self._prompt_template.format(input=input, label=label)
        try:
            pred = await aclassify(prompt, _JUDGE_LABELS)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            return Verdict.UNKNOWN
        return _parse_judge_label(pred.label)


def _parse_judge_label(text: Any) -> Verdict:
    t = str(text).strip().lower()
    if t == "correct":
        return Verdict.CORRECT
    if t == "incorrect":
        return Verdict.INCORRECT
    return Verdict.UNKNOWN


_DEFAULT_JUDGE_PROMPT = (
    "You are evaluating whether a classifier's output is correct.\n"
    "Input: {input!r}\n"
    "Classifier's label: {label!r}\n"
    "Is the label correct for this input? Answer with exactly one "
    'of: "correct", "incorrect", or "unknown" (when the input is '
    "ambiguous or you don't have enough context).\n"
    "Answer:"
)


# ---------------------------------------------------------------------------
# JudgeCommittee — multi-judge aggregation
# ---------------------------------------------------------------------------


_COMMITTEE_MODES = ("majority", "unanimous", "confidence_weighted")


class JudgeCommittee:
    """Aggregate verdicts across multiple model judges.

    Modes
    -----
    - ``"majority"`` — the most-voted verdict wins. Ties go to
      ``UNKNOWN``. Stable on small committees (3, 5, 7 judges).
    - ``"unanimous"`` — all judges must agree on a non-UNKNOWN
      verdict; any disagreement → UNKNOWN. Use when false positives
      are expensive (medical diagnosis, irreversible actions).
    - ``"confidence_weighted"`` — future extension; for v1 this
      falls through to majority. Reserved so callers can pin
      the enum value today.

    Bias guardrail
    --------------
    Every pair of judges is checked against the
    ``require_distinct_from`` classifier (if supplied) using the
    same identity heuristic as :class:`JudgeSource`. A
    committee of clones of the production classifier is a
    degenerate case that the guardrail refuses at construction.
    """

    def __init__(
        self,
        judges: Sequence[ModelClassifier],
        *,
        mode: str = "majority",
        require_distinct_from: ModelClassifier | None = None,
        guard_against_same_llm: bool = True,
        prompt_template: str | None = None,
    ) -> None:
        judge_list = list(judges)
        if len(judge_list) < 2:
            raise ValueError(f"JudgeCommittee requires at least 2 judges; got {len(judge_list)}")
        if mode not in _COMMITTEE_MODES:
            raise ValueError(f"mode must be one of {_COMMITTEE_MODES}; got {mode!r}")
        for j in judge_list:
            if not _is_model_like(j):
                raise TypeError("every committee judge must expose classify() or aclassify()")
        if guard_against_same_llm and require_distinct_from is not None:
            for j in judge_list:
                if _same_model(j, require_distinct_from):
                    cls, model = _identify_model(j)
                    raise ValueError(
                        f"refusing to construct JudgeCommittee: at "
                        f"least one judge resolves to the same language model as "
                        f"require_distinct_from ({cls} / model={model!r}). "
                        f"See JudgeSource for the bias-risk rationale."
                    )
        self._judges = judge_list
        self._mode = mode
        self._template = prompt_template or _DEFAULT_JUDGE_PROMPT
        ids = [_identify_model(j)[1] or _identify_model(j)[0] for j in judge_list]
        self.source_name = f"committee:{'|'.join(ids)}({mode})"

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def judges(self) -> list[ModelClassifier]:
        return list(self._judges)

    def judge(self, input: Any, label: Any, /) -> Verdict:
        prompt = self._template.format(input=input, label=label)
        verdicts: list[Verdict] = []
        for j in self._judges:
            try:
                classify = getattr(j, "classify", None)
                if classify is not None:
                    pred = classify(prompt, _JUDGE_LABELS)
                else:
                    import asyncio

                    pred = asyncio.run(j.aclassify(prompt, _JUDGE_LABELS))
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                verdicts.append(Verdict.UNKNOWN)
                continue
            verdicts.append(_parse_judge_label(pred.label))
        return self._aggregate(verdicts)

    async def ajudge(self, input: Any, label: Any, /) -> Verdict:
        """Async peer of :meth:`judge`.

        Fires every judge in parallel via ``asyncio.gather`` —
        committee latency is ``max(latencies)``, not
        ``sum(latencies)``. Judges that expose ``aclassify`` use
        the native-async path; sync judges fall back to
        ``asyncio.to_thread`` so a mixed-sync-and-async committee
        works.
        """
        import asyncio

        prompt = self._template.format(input=input, label=label)

        async def _one(j: Any) -> Verdict:
            try:
                aclassify = getattr(j, "aclassify", None)
                if aclassify is not None:
                    pred = await aclassify(prompt, _JUDGE_LABELS)
                else:
                    pred = await asyncio.to_thread(
                        j.classify,
                        prompt,
                        _JUDGE_LABELS,
                    )
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                return Verdict.UNKNOWN
            return _parse_judge_label(pred.label)

        verdicts = await asyncio.gather(*(_one(j) for j in self._judges))
        return self._aggregate(list(verdicts))

    def _aggregate(self, verdicts: list[Verdict]) -> Verdict:
        if self._mode == "unanimous":
            first = verdicts[0]
            if first is Verdict.UNKNOWN:
                return Verdict.UNKNOWN
            return first if all(v is first for v in verdicts) else Verdict.UNKNOWN

        # Majority (also the confidence_weighted fallthrough for v1).
        counts: dict[Verdict, int] = {}
        for v in verdicts:
            counts[v] = counts.get(v, 0) + 1
        # Drop UNKNOWN as a tiebreaker candidate unless it's the only option.
        contenders = {k: v for k, v in counts.items() if k is not Verdict.UNKNOWN}
        if not contenders:
            return Verdict.UNKNOWN
        max_votes = max(contenders.values())
        winners = [k for k, v in contenders.items() if v == max_votes]
        if len(winners) != 1:
            return Verdict.UNKNOWN
        return winners[0]


# ---------------------------------------------------------------------------
# HumanReviewerSource — queue-backed manual labeling
# ---------------------------------------------------------------------------


import queue as _queue_mod  # noqa: E402 — kept module-local; HumanReviewerSource is the only consumer


class HumanReviewerSource:
    """Queue-backed verdict source for human reviewers.

    Every ``judge(input, label)`` call pushes a request onto a
    pending queue and then **blocks** until a matching verdict
    appears on a verdicts queue. The reviewer tool (a web UI, a
    Slack bot, a CLI) dequeues from ``pending``, presents the row
    to a human, and puts the resulting verdict back on
    ``verdicts``. A timeout on the blocking wait keeps the switch
    from stalling indefinitely when no reviewer is on shift — on
    timeout the verdict is ``UNKNOWN``.

    For v1 the queues are stdlib ``queue.Queue``. Subclass and
    override :meth:`_push` / :meth:`_pop_verdict` to route through
    Redis, SQS, Kafka, or a reviewer-tool's webhook.

    Intended for ``bulk_record_verdicts_from_source`` cold-start
    pipelines (seed the log with reviewer-labeled rows) and for
    ``export_for_review`` / ``apply_reviews`` periodic-drain
    workflows. Also works inline on ``classify`` for small-volume,
    human-in-the-loop production paths.
    """

    def __init__(
        self,
        *,
        pending: _queue_mod.Queue | None = None,
        verdicts: _queue_mod.Queue | None = None,
        timeout: float = 30.0,
        name: str = "default",
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._pending = pending if pending is not None else _queue_mod.Queue()
        self._verdicts = verdicts if verdicts is not None else _queue_mod.Queue()
        self._timeout = timeout
        self.source_name = f"human-reviewer:{name}"

    @property
    def pending(self) -> _queue_mod.Queue:
        """Queue that reviewer tools consume from. Each item is a
        ``(input, label)`` tuple."""
        return self._pending

    @property
    def verdicts(self) -> _queue_mod.Queue:
        """Queue that reviewer tools produce onto. Each item is a
        :class:`postrule.core.Verdict`."""
        return self._verdicts

    def _push(self, input: Any, label: Any) -> None:
        self._pending.put((input, label))

    def _pop_verdict(self) -> Verdict:
        try:
            return self._verdicts.get(timeout=self._timeout)
        except _queue_mod.Empty:
            return Verdict.UNKNOWN

    def judge(self, input: Any, label: Any, /) -> Verdict:
        self._push(input, label)
        v = self._pop_verdict()
        if isinstance(v, Verdict):
            return v
        # Allow reviewer-tool implementations to put str values for
        # convenience (common when coming off a JSON queue).
        if isinstance(v, str):
            try:
                return Verdict(v)
            except ValueError:
                return Verdict.UNKNOWN
        return Verdict.UNKNOWN


# ---------------------------------------------------------------------------
# WebhookVerdictSource — poll an HTTP endpoint for verdicts
# ---------------------------------------------------------------------------


class WebhookVerdictSource:
    """Poll an HTTP endpoint that returns verdicts for pending inputs.

    The pattern: your external system (ticketing tool, fraud
    detector, downstream consumer) can report outcomes but
    doesn't push them — you pull. Each ``judge(input, label)``
    call performs a POST to ``endpoint`` with a JSON body
    ``{"input": <input>, "label": <label>}`` and parses a response
    body ``{"outcome": "correct"|"incorrect"|"unknown"}``.

    Failure modes handled:

    - HTTP non-2xx, connection error, timeout → ``Verdict.UNKNOWN``
      (external outage must not break the caller's audit loop).
    - Malformed JSON or missing ``outcome`` key → ``Verdict.UNKNOWN``.
    - ``outcome`` value not matching a :class:`Verdict` → ``Verdict.UNKNOWN``.

    Requires :mod:`httpx`. Install with ``pip install postrule[ollama]``
    (same optional dep as the Ollama adapter) or ``pip install httpx``.

    Skeleton: v1 ships the blocking HTTP call. An async peer lands
    in Session 7. For webhook-push semantics (the external system
    POSTs to you), accept the push in your own HTTP route and call
    ``switch.record_verdict`` or ``switch.apply_reviews`` directly.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        timeout: float = 10.0,
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
        name: str | None = None,
    ) -> None:
        try:
            import httpx  # type: ignore[import-untyped]
        except ImportError as e:
            raise ImportError(
                "WebhookVerdictSource requires httpx. "
                "Install with `pip install postrule[ollama]` (shares the "
                "Ollama adapter's optional dep) or `pip install httpx`."
            ) from e
        if not endpoint:
            raise ValueError("endpoint must be a non-empty URL")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._httpx = httpx
        self._endpoint = endpoint
        self._timeout = timeout
        self._headers = headers or {}
        self._auth = auth
        tag = name if name is not None else endpoint
        self.source_name = f"webhook:{tag}"

    def judge(self, input: Any, label: Any, /) -> Verdict:
        try:
            r = self._httpx.post(
                self._endpoint,
                json={"input": input, "label": label},
                headers=self._headers,
                auth=self._auth,
                timeout=self._timeout,
            )
            r.raise_for_status()
            data = r.json()
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            return Verdict.UNKNOWN
        outcome = data.get("outcome") if isinstance(data, dict) else None
        if not isinstance(outcome, str):
            return Verdict.UNKNOWN
        try:
            return Verdict(outcome)
        except ValueError:
            return Verdict.UNKNOWN


# ---------------------------------------------------------------------------
# default_verifier() — auto-detected ship-with-everything default
# ---------------------------------------------------------------------------


class NoVerifierAvailableError(RuntimeError):
    """Raised by :func:`default_verifier` when no language model backend can be
    auto-configured. The exception message lists the recovery options."""


def default_verifier(
    prefer: str = "local",
    *,
    ollama_model: str = "qwen2.5:7b",
    openai_model: str = "gpt-4o-mini",
    anthropic_model: str = "claude-haiku-4-5",
    ollama_host: str = "http://localhost:11434",
) -> JudgeSource:
    """Return a sensibly-configured :class:`JudgeSource`.

    **Local-only by default. No surprise cloud dependency at
    runtime.** ``pip install postrule`` ships standalone — the
    default verifier expects a local Ollama instance and raises
    :class:`NoVerifierAvailableError` with setup instructions
    when one isn't reachable. Cloud verifiers are opt-in via
    ``prefer="openai"`` / ``prefer="anthropic"`` (and a
    corresponding API key in the environment).

    The shipped default is ``qwen2.5:7b`` (~4.7 GB) — picked
    after benchmarking 11 candidate SLMs at n=102 corpus (see
    ``docs/benchmarks/slm-verifier-results.md``). 85% accuracy
    on judged verdicts (the load-bearing metric for the gate);
    above-chance score 0.363, the highest among latency-feasible
    local models. Pull it with ``ollama pull qwen2.5:7b``.

    Verdict latency is ~481 ms p50 — well under the 1-second
    practical ceiling for verifier roles. The DeepSeek-R1 family
    scores higher on accuracy alone (R1-7b: 78% acc, above-chance
    0.421) but at 14 s p50 is disqualified for the verifier role.
    Cloud verifiers (``prefer="openai"`` /
    ``prefer="anthropic"``) are faster and approach the accuracy
    ceiling; the bundled-local path
    (``prefer="bundled"``, lazy-downloaded GGUF served via
    ``llama-cpp-python``) serves the same ``qwen2.5:7b`` weights
    offline without an Ollama install — see :mod:`postrule.bundled`.

    Modes:

    - ``prefer="local"`` (default) — Ollama only. Raises if
      Ollama isn't reachable on ``ollama_host``.
    - ``prefer="bundled"`` — lazy-downloaded GGUF served via
      ``llama-cpp-python``. Requires ``pip install postrule[bundled]``.
      First call pulls ~4.7 GB to
      ``~/.cache/llama.cpp/models/``. See
      :mod:`postrule.bundled`.
    - ``prefer="openai"`` — requires ``OPENAI_API_KEY`` in env.
    - ``prefer="anthropic"`` — requires ``ANTHROPIC_API_KEY`` in env.
    - ``prefer="auto"`` — bundled, then local Ollama, then cloud
      fallbacks (opt-in for users who want best-available).

    Returns the verifier with no self-judgment guardrail wired —
    pass it directly to ``LearnedSwitch(verifier=...)`` and the
    switch construction will refuse if your ``model=`` resolves
    to the same language model (the cross-check happens at switch
    construction; see core.py).
    """
    import os

    options: list[str] = []

    if prefer in ("bundled", "auto"):
        try:
            from postrule.bundled import (
                BundledModelUnavailableError,
                default_verifier_bundled,
            )

            try:
                return default_verifier_bundled()
            except BundledModelUnavailableError as e:
                options.append(f"bundled-model fetch failed: {e}")
            except ImportError:
                options.append("install the bundled extra (`pip install postrule[bundled]`)")
        except ImportError:
            options.append("postrule.bundled is unavailable on this Python version")

        if prefer == "bundled":
            raise NoVerifierAvailableError(
                f"Bundled verifier unavailable. "
                f"Recovery options: {'; '.join(options)}. "
                f"For Ollama instead, pass prefer='local'; for cloud, "
                f"prefer='openai' / 'anthropic'."
            )

    if prefer in ("local", "auto"):
        try:
            import httpx  # type: ignore[import-untyped]

            try:
                r = httpx.get(f"{ollama_host}/api/tags", timeout=1.0)
                if r.status_code == 200:
                    from postrule.models import OllamaAdapter

                    return JudgeSource(OllamaAdapter(model=ollama_model, host=ollama_host))
            except (httpx.ConnectError, httpx.TimeoutException):
                options.append(
                    f"install Ollama (https://ollama.com), start the daemon, "
                    f"and run `ollama pull {ollama_model}` "
                    f"(no API key, zero cost, privacy-preserving)"
                )
        except ImportError:
            options.append("install httpx (`pip install httpx`) to enable Ollama detection")

        if prefer == "local":
            # Local-only mode — don't fall through to cloud.
            raise NoVerifierAvailableError(
                f"No local SLM verifier reachable. "
                f"Recovery options: {'; '.join(options)}. "
                f"For an opt-in cloud fallback, pass "
                f"prefer='auto' / 'openai' / 'anthropic'."
            )

    if prefer in ("auto", "openai"):
        if os.getenv("OPENAI_API_KEY"):
            try:
                from postrule.models import OpenAIAdapter

                return JudgeSource(OpenAIAdapter(model=openai_model))
            except ImportError:
                options.append("install the OpenAI extra (`pip install postrule[openai]`)")
        else:
            options.append("set OPENAI_API_KEY in your environment")

    if prefer in ("auto", "anthropic"):
        if os.getenv("ANTHROPIC_API_KEY"):
            try:
                from postrule.models import AnthropicAdapter

                return JudgeSource(AnthropicAdapter(model=anthropic_model))
            except ImportError:
                options.append("install the Anthropic extra (`pip install postrule[anthropic]`)")
        else:
            options.append("set ANTHROPIC_API_KEY in your environment")

    raise NoVerifierAvailableError(
        f"No language model verifier could be auto-configured (prefer={prefer!r}). "
        f"Recovery options: {'; '.join(options) or 'pass verifier= explicitly'}."
    )


__all__ = [
    "CallableVerdictSource",
    "HumanReviewerSource",
    "JudgeCommittee",
    "JudgeSource",
    "NoVerifierAvailableError",
    "VerdictSource",
    "WebhookVerdictSource",
    "default_verifier",
]
