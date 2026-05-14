# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""The ``@ml_switch`` decorator — generic, brand-neutral public API.

Wraps a user function (the rule) as a :class:`LearnedSwitch` while
keeping the decorated name callable exactly like the original function.

    @ml_switch(labels=["bug", "feature"], author="alice")
    def triage(ticket):
        if "crash" in ticket.get("title", ""):
            return "bug"
        return "feature"

    # Still a regular call:
    label = triage({"title": "app crashes"})   # → "bug"

    # Plus LearnedSwitch affordances:
    triage.record_verdict(input={...}, label="bug", outcome="correct")
    triage.status()
    triage.switch   # the underlying LearnedSwitch instance
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from postrule._packing import introspect_signature
from postrule.core import (
    ClassificationResult,
    Label,
    LabelsArg,
    LearnedSwitch,
    Phase,
    SwitchConfig,
)


class _MLSwitchWrapper:
    """Callable + proxy for a decorated function.

    Exists as a class (not a closure) so introspection tools see a
    stable object with predictable attribute access.
    """

    def __init__(
        self,
        fn: Callable[..., Any],
        switch: LearnedSwitch,
        packed_signature: Any | None = None,
    ) -> None:
        self.switch = switch
        self._fn = fn
        self._packed_signature = packed_signature
        # Preserve wrapped function metadata so reflection / help()
        # / docstrings work as if the user had called the bare fn.
        functools.update_wrapper(self, fn)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._fn(*args, **kwargs)

    # Proxy convenience methods onto the wrapped switch.

    @property
    def name(self) -> str:
        return self.switch.name

    def classify(self, *args: Any, **kwargs: Any) -> ClassificationResult:
        """Pure classification, no side effects.

        Accepts the original function's positional + keyword args; the
        wrapper packs them into the synthetic input dataclass before
        calling :meth:`LearnedSwitch.classify`. Single-arg rules stay
        on the fast path, so existing callers see no behavior change.
        See :meth:`LearnedSwitch.classify`.
        """
        packed = self._packed_signature.pack(args, kwargs)
        return self.switch.classify(packed)

    def dispatch(self, *args: Any, **kwargs: Any) -> ClassificationResult:
        """Classify + fire the matched label's action.

        Accepts the original function's positional + keyword args (see
        :meth:`classify`). See :meth:`LearnedSwitch.dispatch`.
        """
        packed = self._packed_signature.pack(args, kwargs)
        return self.switch.dispatch(packed)

    def record_verdict(
        self,
        *,
        input: Any,
        label: Any,
        outcome: str,
        source: str = "rule",
        confidence: float = 1.0,
    ) -> None:
        self.switch.record_verdict(
            input=input,
            label=label,
            outcome=outcome,
            source=source,
            confidence=confidence,
        )

    def status(self):  # → SwitchStatus
        return self.switch.status()

    def phase(self):  # → Phase
        return self.switch.phase()


def ml_switch(
    *,
    labels: LabelsArg | None = None,
    author: str | None = None,
    name: str | None = None,
    # Hoisted SwitchConfig fields — the common case. Either use these,
    # or pass an explicit ``config=SwitchConfig(...)``, but not both.
    starting_phase: Phase | None = None,
    phase_limit: Phase | None = None,
    safety_critical: bool | None = None,
    confidence_threshold: float | None = None,
    gate: Any | None = None,
    auto_record: bool | None = None,
    auto_advance: bool | None = None,
    auto_advance_interval: int | None = None,
    on_verdict: Callable[[Any], None] | None = None,
    verifier: Any = None,
    verifier_sample_rate: float | None = None,
    config: SwitchConfig | None = None,
    storage: Any | None = None,
    persist: bool = False,
    model: Any | None = None,
    ml_head: Any | None = None,
    telemetry: Any | None = None,
) -> Callable[[Callable[..., Any]], _MLSwitchWrapper]:
    """Wrap a classification rule function as a :class:`LearnedSwitch`.

    Args:
        labels: The switch's label-based conditional expressions —
            each label names a possible classifier output; pairing a
            label with an ``on=`` action turns the label into a
            dispatch clause that Postrule evaluates on match. Accepted
            forms: ``list[str]`` (plain labels), ``list[Label]``
            (labels with optional actions), or ``dict[str, Callable]``
            (shorthand for per-label actions). Optional at Phase 0;
            required at Phase 1+ for language model/ML routing.
        author: Optional provenance string (team handle, service
            account, compliance ID). When omitted, auto-derived
            from the decorated function's module plus its name as
            ``"@<module>:<function>"`` — stable per deployment,
            unique per-switch-per-module. Pass explicitly to use
            a custom scheme.
        name: Stable switch identifier. Defaults to the wrapped
            function's ``__name__``.
        config: Optional :class:`SwitchConfig`.
        storage: Optional :class:`Storage` backend.
        model: Optional :class:`ModelClassifier` used in MODEL_SHADOW /
            MODEL_PRIMARY phases.

    Returns a wrapper callable that forwards to the decorated function
    and exposes the LearnedSwitch affordances (``record_verdict``,
    ``status``, ``phase``, ``switch``).
    """

    def decorate(fn: Callable[..., Any]) -> _MLSwitchWrapper:
        switch_name = name or fn.__name__

        # Introspect the user's rule to learn its arg list and build
        # the synthetic packed-input dataclass. Single-arg rules take
        # the passthrough fast path (no packing); multi-arg rules
        # require annotations on every parameter.
        packed_sig = introspect_signature(fn, class_name=switch_name)

        if packed_sig.is_single_passthrough:
            # Preserve the existing single-positional behavior bit-for-
            # bit: the rule LearnedSwitch holds is just ``fn``, and any
            # user-provided ``on=`` callables are passed through.
            inner_rule = fn
            wrapped_labels = labels
        else:

            def inner_rule(packed: Any) -> Any:
                a, kw = packed_sig.unpack(packed)
                return fn(*a, **kw)

            wrapped_labels = _wrap_on_callables(labels, packed_sig)

        switch = LearnedSwitch(
            name=switch_name,
            rule=inner_rule,
            author=author,
            labels=wrapped_labels,
            starting_phase=starting_phase,
            phase_limit=phase_limit,
            safety_critical=safety_critical,
            confidence_threshold=confidence_threshold,
            gate=gate,
            auto_record=auto_record,
            auto_advance=auto_advance,
            auto_advance_interval=auto_advance_interval,
            on_verdict=on_verdict,
            verifier=verifier,
            verifier_sample_rate=verifier_sample_rate,
            config=config,
            storage=storage,
            persist=persist,
            model=model,
            ml_head=ml_head,
            telemetry=telemetry,
        )
        return _MLSwitchWrapper(fn, switch, packed_signature=packed_sig)

    return decorate


def _wrap_on_callables(labels: LabelsArg | None, packed_sig: Any) -> LabelsArg | None:
    """Wrap each user-provided ``on=`` callable so it sees the original
    positional args (not the packed input). Required because
    :meth:`LearnedSwitch._maybe_dispatch` calls ``label.on(input)`` with
    the packed dataclass as its single argument.
    """
    if labels is None:
        return None
    if isinstance(labels, dict):
        out: dict[str, Any] = {}
        for label_name, on_callable in labels.items():
            if on_callable is None:
                out[label_name] = None
            else:
                out[label_name] = _make_unpacking_on(on_callable, packed_sig)
        return out
    if isinstance(labels, list):
        new_list: list[Any] = []
        for item in labels:
            if isinstance(item, str):
                new_list.append(item)
                continue
            if isinstance(item, Label):
                if item.on is None:
                    new_list.append(item)
                else:
                    new_list.append(
                        Label(
                            name=item.name,
                            on=_make_unpacking_on(item.on, packed_sig),
                        )
                    )
                continue
            new_list.append(item)
        return new_list
    return labels


def _make_unpacking_on(on_callable: Callable[..., Any], packed_sig: Any) -> Callable[[Any], Any]:
    """Return a single-arg adapter that unpacks the packed input back to
    the user-callable's original ``(*args, **kwargs)``.
    """

    def _adapter(packed: Any) -> Any:
        a, kw = packed_sig.unpack(packed)
        return on_callable(*a, **kw)

    # Preserve the original callable's name for telemetry / debugging.
    try:
        _adapter.__name__ = getattr(on_callable, "__name__", "on")
        _adapter.__qualname__ = getattr(on_callable, "__qualname__", "on")
    except Exception:
        pass
    return _adapter
