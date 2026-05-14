# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Multi-arg packing helpers for ``@ml_switch`` and ``postrule.Switch``.

When a user's rule (or a Switch subclass's evidence method) takes more
than one positional argument, Postrule builds a synthetic packed-input
dataclass at decoration / class-creation time. The dataclass has one
field per parameter, typed by the parameter's annotation. The wrapping
machinery packs args/kwargs into this dataclass before handing them to
the LLM/ML head, and unpacks them again when calling user code (the
rule body, the on= action handler, an evidence gatherer).

Single-positional-arg signatures take a fast path: no packing is
performed and existing usage is preserved bit-for-bit. This module
exposes two pieces of state per inspected signature:

* a packed-input dataclass type (``packed_class``)
* a callable :class:`PackedSignature` that turns ``(*args, **kwargs)``
  into a ``packed_class`` instance and back.
"""

from __future__ import annotations

import inspect
import typing
from collections.abc import Callable
from dataclasses import dataclass, field, is_dataclass, make_dataclass
from typing import Any

# Sentinel used in dataclass fields that have no default.
_NO_DEFAULT = object()


@dataclass
class PackedSignature:
    """Introspected signature with a paired packed-input dataclass.

    Attributes:
        params: ordered parameter names (exclusive of ``self``).
        annotations: ``{param_name: type}`` for every named param.
        defaults: ``{param_name: default}`` for params with defaults.
        var_positional: name of the ``*args`` param, if any.
        var_keyword: name of the ``**kwargs`` param, if any.
        packed_class: the synthetic dataclass.
        is_single_passthrough: when ``True``, the signature has exactly
            one positional arg and no annotation was required, so
            packing is a no-op (bool ``True`` is the fast path flag;
            packed_class still exists for introspection consistency).
    """

    params: tuple[str, ...]
    annotations: dict[str, type]
    defaults: dict[str, Any]
    var_positional: str | None
    var_keyword: str | None
    packed_class: type
    is_single_passthrough: bool

    def pack(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        """Bind ``(args, kwargs)`` into a ``packed_class`` instance.

        For the single-passthrough fast path, this returns ``args[0]``
        unchanged so existing single-positional callers see no behavior
        difference.
        """
        if self.is_single_passthrough and args:
            # Pure passthrough; do not wrap a single positional arg.
            return args[0]
        # Single named param supplied via kwargs — fall through to
        # general path below using the packed dataclass.

        kwargs_in = dict(kwargs)
        bound: dict[str, Any] = {}

        # Walk named (non-var) params in declaration order, consuming
        # positional first then named.
        named_params = [
            p for p in self.params if p != self.var_positional and p != self.var_keyword
        ]
        positional_iter = iter(args)
        consumed = 0
        for name in named_params:
            try:
                bound[name] = next(positional_iter)
                consumed += 1
            except StopIteration as exc:
                if name in kwargs_in:
                    bound[name] = kwargs_in.pop(name)
                elif name in self.defaults:
                    bound[name] = self.defaults[name]
                else:
                    raise TypeError(f"missing required positional argument: {name!r}") from exc

        # Capture any remaining positional args under *args field.
        remaining_positional = tuple(positional_iter)
        if self.var_positional is not None:
            bound[self.var_positional] = remaining_positional
        elif remaining_positional:
            raise TypeError(f"unexpected positional argument(s): {remaining_positional!r}")

        # Capture any remaining kwargs under **kwargs field.
        if self.var_keyword is not None:
            bound[self.var_keyword] = kwargs_in
        elif kwargs_in:
            raise TypeError(f"unexpected keyword argument(s): {tuple(kwargs_in)!r}")

        return self.packed_class(**bound)

    def unpack(self, packed: Any) -> tuple[tuple[Any, ...], dict[str, Any]]:
        """Inverse of :meth:`pack`. Returns ``(args, kwargs)`` such that
        calling ``user_fn(*args, **kwargs)`` reproduces the original call.
        """
        if self.is_single_passthrough:
            return (packed,), {}

        args_out: list[Any] = []
        named_params = [
            p for p in self.params if p != self.var_positional and p != self.var_keyword
        ]
        for name in named_params:
            args_out.append(getattr(packed, name))
        if self.var_positional is not None:
            args_out.extend(getattr(packed, self.var_positional))
        kwargs_out: dict[str, Any] = {}
        if self.var_keyword is not None:
            kwargs_out = dict(getattr(packed, self.var_keyword))
        return tuple(args_out), kwargs_out


def introspect_signature(
    fn: Callable[..., Any],
    *,
    skip_self: bool = False,
    class_name: str | None = None,
    require_annotations: str = "multi-arg-only",
) -> PackedSignature:
    """Introspect ``fn`` and build a paired packed-input dataclass.

    Args:
        fn: the user function or unbound method.
        skip_self: when ``True``, ignore the first positional parameter
            (used for instance methods).
        class_name: prefix for the synthetic dataclass name, for nicer
            debugging. Defaults to ``fn.__name__``.
        require_annotations: ``"multi-arg-only"`` (default) raises only
            when a function has more than one parameter and any of them
            lacks an annotation. ``"always"`` raises whenever any
            parameter lacks an annotation. ``"never"`` skips the check
            (synthetic dataclass falls back to ``Any``).
    """
    sig = inspect.signature(fn)
    parameters = list(sig.parameters.values())
    if skip_self and parameters:
        parameters = parameters[1:]

    params: list[str] = []
    annotations: dict[str, type] = {}
    defaults: dict[str, Any] = {}
    var_positional: str | None = None
    var_keyword: str | None = None

    # Collect type hints once so forward refs (e.g., ``from __future__
    # import annotations``) are resolved consistently.
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        hints = {}

    for p in parameters:
        params.append(p.name)
        if p.kind is inspect.Parameter.VAR_POSITIONAL:
            var_positional = p.name
        elif p.kind is inspect.Parameter.VAR_KEYWORD:
            var_keyword = p.name
        else:
            if p.default is not inspect.Parameter.empty:
                defaults[p.name] = p.default
        # Annotation: prefer resolved hint; fall back to raw annotation.
        if p.annotation is not inspect.Parameter.empty:
            annotations[p.name] = hints.get(p.name, p.annotation)

    named_count = sum(
        1
        for p in parameters
        if p.kind
        not in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        )
    )
    total_count = len(parameters)

    # Annotation enforcement: kicks in only for multi-arg signatures
    # (or always, depending on the policy). Single-arg signatures stay
    # back-compatible for callers that never typed their rules.
    if require_annotations != "never":
        check_all = require_annotations == "always" or total_count > 1
        if check_all:
            for p in parameters:
                if p.kind in (
                    inspect.Parameter.VAR_POSITIONAL,
                    inspect.Parameter.VAR_KEYWORD,
                ):
                    # *args / **kwargs need an inner annotation but are
                    # generally allowed without one (their packed type
                    # is tuple / dict by construction).
                    continue
                if p.name not in annotations:
                    where = f"{class_name}.{fn.__name__}" if class_name is not None else fn.__name__
                    raise TypeError(
                        f"{where!r} has parameter {p.name!r} with no "
                        "annotation; multi-argument rules and evidence "
                        "methods require type annotations on every "
                        "parameter so the packed-input schema is fully "
                        "typed (the LLM/ML head needs the schema)."
                    )

    # Decide if we can use the fast single-arg passthrough path.
    is_single_passthrough = named_count == 1 and var_positional is None and var_keyword is None

    # Build a dataclass even on the passthrough path so introspection
    # works uniformly (e.g., tests that read ``_packed_input_class``).
    field_specs: list[tuple[str, Any, Any]] = []
    for name in params:
        if name == var_positional:
            tp = tuple
            field_specs.append((name, tp, field(default_factory=tuple)))
            continue
        if name == var_keyword:
            tp = dict
            field_specs.append((name, tp, field(default_factory=dict)))
            continue
        tp = annotations.get(name, Any)
        if name in defaults:
            field_specs.append((name, tp, field(default=defaults[name])))
        else:
            field_specs.append((name, tp))

    cls_prefix = class_name or fn.__name__
    packed_cls = make_dataclass(
        f"{cls_prefix}_PackedInput",
        # make_dataclass takes either (name, type) or (name, type, field)
        list(field_specs),
    )

    return PackedSignature(
        params=tuple(params),
        annotations=annotations,
        defaults=defaults,
        var_positional=var_positional,
        var_keyword=var_keyword,
        packed_class=packed_cls,
        is_single_passthrough=is_single_passthrough,
    )


def signatures_match(a: inspect.Signature, b: inspect.Signature, *, skip_self: bool = True) -> bool:
    """Return ``True`` iff ``a`` and ``b`` declare the same positional
    parameters (names + kinds + annotations + defaults), ignoring the
    leading ``self`` when ``skip_self`` is True.
    """

    def _normalize(sig: inspect.Signature) -> list[tuple]:
        params = list(sig.parameters.values())
        if skip_self and params:
            params = params[1:]
        out = []
        for p in params:
            ann = p.annotation if p.annotation is not inspect.Parameter.empty else None
            default = p.default if p.default is not inspect.Parameter.empty else _NO_DEFAULT
            out.append((p.name, p.kind, ann, default))
        return out

    return _normalize(a) == _normalize(b)


# Re-export ``is_dataclass`` so callers don't need to import dataclasses
# alongside _packing in the typical case.
__all__ = ["PackedSignature", "introspect_signature", "signatures_match", "is_dataclass"]
