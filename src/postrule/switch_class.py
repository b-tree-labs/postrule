# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Native authoring pattern: subclass :class:`Switch` instead of decorating
a function with :func:`ml_switch`.

The convention:

- Methods named ``_evidence_<name>`` produce a typed field on the
  evidence dataclass. Their return-type annotation is the field type.
- Either a single ``_rule(self, evidence)`` method, OR per-label
  ``_when_<label>(self, evidence) -> bool`` predicates. Not both.
- Methods named ``_on_<label>`` are auto-bound to the matching label;
  they receive the original input the switch was called with.
- A nested ``class Meta:`` may declare ``default_label`` (used when no
  ``_when_*`` predicate matches) and ``no_action`` (an iterable of
  label names known to be returned by ``_rule`` but with no handler).

The base class introspects the subclass at ``__init_subclass__`` and
constructs an inner :class:`~postrule.core.LearnedSwitch` lazily at
``__init__`` time. Instance methods (``classify``, ``dispatch``,
``adispatch``, ``advance``, ``phase``, ``name``) proxy to the inner
switch, so a ``Switch`` subclass quacks like a ``LearnedSwitch`` for
all production-path uses.
"""

from __future__ import annotations

import inspect
import typing
from collections.abc import Callable
from dataclasses import make_dataclass
from typing import Any

from postrule._packing import PackedSignature, introspect_signature, signatures_match
from postrule.core import Label, LearnedSwitch

# Sentinel marker on the abstract base class so __init_subclass__ skips
# its own introspection. Subclasses go through the full pipeline.
_BASE_MARKER = "__postrule_switch_base__"


class Switch:
    """Convention-driven base class for authoring a Postrule switch as a
    Python class.

    Subclasses define evidence gatherers, a rule (or per-label
    predicates), and action handlers as methods following the
    documented naming conventions. The class introspects itself at
    creation time; instances construct a backing
    :class:`~postrule.core.LearnedSwitch` and proxy its public surface.
    """

    # Marker so __init_subclass__ knows to skip its own introspection.
    locals()[_BASE_MARKER] = True

    # ------- populated by __init_subclass__ on each user subclass -------
    _evidence_method_names: dict[str, str]  # field_name -> method name
    _evidence_class: type  # auto-built dataclass
    _rule_method_name: str | None  # "_rule" if defined, else None
    _when_method_names: dict[str, str]  # label -> "_when_<label>"
    _on_method_names: dict[str, str]  # label -> "_on_<label>"
    _meta_default_label: str | None
    _meta_no_action: tuple[str, ...]
    # Public-facing call signature shared by every _evidence_* and
    # _on_<label> method (excluding self). When the user's switch takes
    # a single positional arg, _input_signature is None — that's the
    # back-compat fast path. Otherwise it carries the packed dataclass
    # so classify/dispatch can convert (args, kwargs) → packed input.
    _input_signature: PackedSignature | None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Skip the base class itself (it carries the marker in __dict__).
        # Subclasses inherit but do not redeclare the marker, so it's
        # only present on Switch itself.
        if cls.__dict__.get(_BASE_MARKER, False):
            return

        # Walk the MRO so inherited methods (from a Switch subclass that
        # extends another Switch subclass) are picked up.
        all_methods = _collect_methods(cls)
        cls._evidence_method_names = _gather_evidence_methods(all_methods)
        cls._evidence_class = _build_evidence_class(
            cls.__name__, cls._evidence_method_names, all_methods
        )
        cls._rule_method_name, cls._when_method_names = _gather_rule_or_when(
            cls.__name__, all_methods
        )
        cls._on_method_names = _gather_on_methods(all_methods)
        cls._meta_default_label, cls._meta_no_action = _collect_meta(cls)
        # Detect the shared public input signature from the _evidence_*
        # methods (and validate consistency). Single-arg signatures
        # take a back-compat passthrough (None); multi-arg signatures
        # build a packed-input dataclass that the LLM/ML head will see.
        cls._input_signature = _detect_input_signature(cls, all_methods)
        _validate_structure(cls)

    def __init__(self, **kwargs: Any) -> None:
        # Default the switch name to the class name; allow override via
        # name= kwarg (passed through to LearnedSwitch).
        kwargs.setdefault("name", self.__class__.__name__)

        rule_func = self._build_inner_rule()
        labels = self._build_labels_list()

        self._inner = LearnedSwitch(
            rule=rule_func,
            labels=labels,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Internal: construct the rule callable that LearnedSwitch holds.
    # ------------------------------------------------------------------

    def _build_inner_rule(self) -> Callable[[Any], Any]:
        """Return a callable input → label, threading evidence gathering
        through the subclass's _evidence_* / _rule (or _when_*) methods.

        For multi-arg switches the ``input_obj`` LearnedSwitch passes in
        is a packed dataclass; evidence methods take the original
        positional args, so we unpack before calling each one.
        """
        evidence_class = self._evidence_class
        ev_method_names = self._evidence_method_names
        rule_name = self._rule_method_name
        when_names = self._when_method_names
        default_label = self._meta_default_label
        input_sig = self._input_signature

        def _rule(input_obj: Any) -> Any:
            if input_sig is None or input_sig.is_single_passthrough:
                ev_args: tuple = (input_obj,)
                ev_kwargs: dict = {}
            else:
                ev_args, ev_kwargs = input_sig.unpack(input_obj)
            evidence_kwargs = {
                field_name: getattr(self, method_name)(*ev_args, **ev_kwargs)
                for field_name, method_name in ev_method_names.items()
            }
            evidence = evidence_class(**evidence_kwargs)
            if rule_name is not None:
                return getattr(self, rule_name)(evidence)
            for label, method_name in when_names.items():
                if getattr(self, method_name)(evidence):
                    return label
            return default_label

        # Give the rule a stable __name__ so LearnedSwitch's name
        # auto-derivation (when name= isn't passed) lines up.
        _rule.__name__ = self.__class__.__name__
        return _rule

    def _build_labels_list(self) -> list[Label]:
        """Build the labels= argument for LearnedSwitch from _on_* methods,
        _when_* method names, default_label, and no_action.
        """
        # Union of every label name we know about. _rule's actual returns
        # are dynamic; for the _rule form, _on_* names are the implicit
        # declaration. For the _when_* form, the _when_<label> names are
        # the declaration.
        all_labels: set[str] = set()
        all_labels.update(self._on_method_names.keys())
        all_labels.update(self._when_method_names.keys())
        if self._meta_default_label is not None:
            all_labels.add(self._meta_default_label)
        all_labels.update(self._meta_no_action)

        input_sig = self._input_signature
        labels: list[Label] = []
        for label in sorted(all_labels):
            method_name = self._on_method_names.get(label)
            on_callable: Callable[[Any], Any] | None = None
            if method_name is not None:
                bound = getattr(self, method_name)
                if input_sig is None or input_sig.is_single_passthrough:
                    on_callable = bound
                else:
                    on_callable = _make_unpacking_method_adapter(bound, input_sig)
            labels.append(Label(name=label, on=on_callable))
        return labels

    # ------------------------------------------------------------------
    # Proxied LearnedSwitch surface — the production-path verbs.
    # ------------------------------------------------------------------

    def classify(self, *args: Any, **kwargs: Any) -> Any:
        packed = self._pack_inputs(args, kwargs)
        return self._inner.classify(packed)

    def dispatch(self, *args: Any, **kwargs: Any) -> Any:
        packed = self._pack_inputs(args, kwargs)
        return self._inner.dispatch(packed)

    async def aclassify(self, *args: Any, **kwargs: Any) -> Any:
        packed = self._pack_inputs(args, kwargs)
        return await self._inner.aclassify(packed)

    async def adispatch(self, *args: Any, **kwargs: Any) -> Any:
        packed = self._pack_inputs(args, kwargs)
        return await self._inner.adispatch(packed)

    def _pack_inputs(self, args: tuple, kwargs: dict) -> Any:
        """Bridge the public ``classify(*args, **kwargs)`` surface to the
        single-input shape :class:`LearnedSwitch` expects. Single-arg
        switches pass through unchanged for back-compat.
        """
        sig = self._input_signature
        if sig is None or sig.is_single_passthrough:
            # Back-compat fast path: single positional input is passed
            # straight to LearnedSwitch.
            if len(args) == 1 and not kwargs:
                return args[0]
            if not args and len(kwargs) == 1:
                return next(iter(kwargs.values()))
            # Fall through to the packed path if a passthrough switch
            # somehow received unexpected shapes; let pack() raise a
            # clear error rather than silently swallowing the issue.
        return sig.pack(args, kwargs)

    def advance(self, **kwargs: Any) -> Any:
        return self._inner.advance(**kwargs)

    def phase(self) -> Any:
        return self._inner.phase()

    def record_verdict(self, *args: Any, **kwargs: Any) -> Any:
        return self._inner.record_verdict(*args, **kwargs)

    async def arecord_verdict(self, *args: Any, **kwargs: Any) -> Any:
        return await self._inner.arecord_verdict(*args, **kwargs)

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def author(self) -> str:
        return self._inner.author

    @property
    def config(self) -> Any:
        return self._inner.config


# ----------------------------------------------------------------------
# Module-private introspection helpers — operate on a class object.
# ----------------------------------------------------------------------


def _collect_methods(cls: type) -> dict[str, Callable]:
    """Return ``{method_name: function}`` for every callable on the class
    or its non-Switch ancestors. Walks the MRO so inherited methods are
    picked up; later-declared classes override earlier ones (standard
    Python MRO semantics).
    """
    out: dict[str, Callable] = {}
    for ancestor in reversed(cls.__mro__):
        if ancestor is Switch or ancestor is object:
            continue
        for attr_name, attr_value in ancestor.__dict__.items():
            if callable(attr_value) and not isinstance(attr_value, type):
                out[attr_name] = attr_value
    return out


def _gather_evidence_methods(methods: dict[str, Callable]) -> dict[str, str]:
    """Find ``_evidence_<name>`` methods and validate they have return
    annotations. Returns ``{field_name: method_name}``.
    """
    out: dict[str, str] = {}
    for method_name in methods:
        if not method_name.startswith("_evidence_"):
            continue
        field_name = method_name[len("_evidence_") :]
        if not field_name:
            raise TypeError(
                f"Switch evidence method {method_name!r} has no field name "
                "after the '_evidence_' prefix."
            )
        out[field_name] = method_name
    return out


def _build_evidence_class(
    class_name: str,
    evidence_methods: dict[str, str],
    methods: dict[str, Callable],
) -> type:
    """Build a dataclass with one field per evidence method, typed by the
    method's return annotation. Raise if any evidence method lacks one.
    """
    fields: list[tuple[str, type]] = []
    for field_name, method_name in evidence_methods.items():
        method = methods[method_name]
        # First confirm the source annotation exists. Use signature for
        # the empty-vs-present check; use get_type_hints for the resolved
        # type (which evaluates forward refs from `from __future__ import
        # annotations`).
        sig = inspect.signature(method)
        if sig.return_annotation is inspect.Signature.empty:
            raise TypeError(
                f"Switch evidence method {method_name!r} on "
                f"{class_name!r} has no return annotation. "
                "Add a return type hint so the evidence dataclass field "
                "can be typed (the LLM/ML head needs the schema)."
            )
        try:
            hints = typing.get_type_hints(method)
        except Exception as e:
            raise TypeError(
                f"Could not resolve return-type annotation for "
                f"{method_name!r} on {class_name!r}: {e}"
            ) from e
        resolved = hints.get("return", sig.return_annotation)
        fields.append((field_name, resolved))
    return make_dataclass(f"{class_name}Evidence", fields, frozen=True)


def _gather_rule_or_when(
    class_name: str, methods: dict[str, Callable]
) -> tuple[str | None, dict[str, str]]:
    """Detect either a ``_rule`` method or per-label ``_when_<label>``
    methods. Exactly one form must be present. Returns
    ``(rule_method_name_or_None, when_methods_in_declaration_order)``.
    """
    has_rule = "_rule" in methods
    when_methods: dict[str, str] = {}
    for method_name in methods:
        if not method_name.startswith("_when_"):
            continue
        label = method_name[len("_when_") :]
        if not label:
            raise TypeError(
                f"Switch when method {method_name!r} has no label name after the '_when_' prefix."
            )
        when_methods[label] = method_name

    if has_rule and when_methods:
        raise TypeError(
            f"Switch subclass {class_name!r} declares both _rule and "
            f"_when_* methods ({sorted(when_methods)}). Use one form, "
            "not both."
        )
    if not has_rule and not when_methods:
        raise TypeError(
            f"Switch subclass {class_name!r} declares neither _rule nor "
            "any _when_<label> methods. Define one of these forms so "
            "the switch knows how to pick a label."
        )
    return ("_rule" if has_rule else None, when_methods)


def _gather_on_methods(methods: dict[str, Callable]) -> dict[str, str]:
    """Find ``_on_<label>`` methods. Returns ``{label: method_name}``."""
    out: dict[str, str] = {}
    for method_name in methods:
        if not method_name.startswith("_on_"):
            continue
        label = method_name[len("_on_") :]
        if not label:
            raise TypeError(
                f"Switch action method {method_name!r} has no label name after the '_on_' prefix."
            )
        out[label] = method_name
    return out


def _collect_meta(cls: type) -> tuple[str | None, tuple[str, ...]]:
    """Read ``class Meta:`` on the subclass for default_label + no_action.
    Walks the MRO so a child can override a parent's Meta.
    """
    default_label: str | None = None
    no_action: tuple[str, ...] = ()
    for ancestor in cls.__mro__:
        meta = ancestor.__dict__.get("Meta")
        if meta is None:
            continue
        if hasattr(meta, "default_label") and default_label is None:
            default_label = meta.default_label
        if hasattr(meta, "no_action") and not no_action:
            no_action = tuple(meta.no_action)
        # Stop once any Meta was found (closest wins, partial ok).
        break
    return default_label, no_action


def _detect_input_signature(cls: type, methods: dict[str, Callable]) -> PackedSignature | None:
    """Read the public input signature off the subclass's ``_evidence_*``
    methods. All evidence methods must declare the same positional
    parameters (excluding ``self``); a mismatch is a class-definition-
    time error, not a dispatch-time surprise.

    Returns ``None`` when no evidence methods exist (lets the rest of
    ``__init_subclass__`` raise its existing missing-_rule error
    cleanly), or when the inferred signature is a single positional
    arg (the back-compat fast path).
    """
    ev_methods = [m for name, m in methods.items() if name.startswith("_evidence_")]
    if not ev_methods:
        return None

    # Pin the first evidence method's signature as the reference.
    reference = ev_methods[0]
    ref_sig = inspect.signature(reference)
    for other in ev_methods[1:]:
        other_sig = inspect.signature(other)
        if not signatures_match(ref_sig, other_sig, skip_self=True):
            raise TypeError(
                f"Switch subclass {cls.__name__!r}: evidence methods "
                f"declare inconsistent signatures (got "
                f"{_describe_sig(ref_sig)} and {_describe_sig(other_sig)}). "
                "All _evidence_* methods on a single Switch must take "
                "the same positional arguments so the packed input is "
                "well-defined."
            )

    # Build a packed signature from the reference. Skip the leading
    # ``self`` parameter; require annotations on all non-self params
    # for multi-arg signatures.
    return introspect_signature(
        reference,
        skip_self=True,
        class_name=cls.__name__,
        require_annotations="multi-arg-only",
    )


def _describe_sig(sig: inspect.Signature) -> str:
    """Human-readable signature label for error messages."""
    parts = []
    for p in list(sig.parameters.values())[1:]:  # skip self
        parts.append(p.name)
    return "(" + ", ".join(parts) + ")"


def _make_unpacking_method_adapter(
    bound_method: Callable[..., Any],
    sig: PackedSignature,
) -> Callable[[Any], Any]:
    """Return a single-arg adapter for an ``_on_<label>`` method whose
    real signature is ``(self, arg1, arg2, ...)``. Unpacks the packed
    input back into the original positional + keyword args before
    calling the bound method.
    """

    def _adapter(packed: Any) -> Any:
        args, kwargs = sig.unpack(packed)
        return bound_method(*args, **kwargs)

    try:
        _adapter.__name__ = getattr(bound_method, "__name__", "on")
        _adapter.__qualname__ = getattr(bound_method, "__qualname__", "on")
    except Exception:
        pass
    return _adapter


def _validate_structure(cls: type) -> None:
    """Cross-check evidence + rule/when + on + meta for orphans and
    obvious misconfigurations. Errors here fire at class-definition
    time, not at first dispatch.
    """
    # Orphaned _on_<label> validation only applies to the _when_* form,
    # because _rule's returns are dynamic and we can't enumerate them.
    if cls._rule_method_name is None:
        valid_labels: set[str] = set(cls._when_method_names.keys())
        if cls._meta_default_label is not None:
            valid_labels.add(cls._meta_default_label)
        valid_labels.update(cls._meta_no_action)
        orphans = sorted(label for label in cls._on_method_names if label not in valid_labels)
        if orphans:
            label = orphans[0]
            raise TypeError(
                f"Switch subclass {cls.__name__!r} has _on_{label} "
                f"but no _when_{label} (and {label!r} is not in "
                "Meta.default_label or Meta.no_action). Either "
                f"declare _when_{label} or remove the orphan handler."
            )
