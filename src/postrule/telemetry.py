# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Telemetry hooks — zero-dep, opt-in.

Every :class:`~postrule.core.LearnedSwitch` can be configured with a
:class:`TelemetryEmitter`. The switch emits two event kinds:

- ``classify``  — one per :meth:`~postrule.core.LearnedSwitch.classify`
- ``outcome``   — one per :meth:`~postrule.core.LearnedSwitch.record_verdict`

An emitter is just a callable ``emit(event_name, payload_dict)``. The
library never blocks on telemetry and swallows exceptions so a broken
emitter can't degrade the decision path.

The default emitter is :class:`NullEmitter` for an unconfigured switch.
Optional integrations (see :mod:`postrule.cloud.verdict_telemetry`) can
register a process-wide default emitter via
:func:`register_default_emitter` so signed-in users automatically get
their verdicts streamed to the hosted API without any code change.

The ``POSTRULE_NO_TELEMETRY`` environment variable short-circuits the
default-emitter lookup unconditionally — useful for CI, smoke tests,
and operators who want belt-and-suspenders opt-out.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TelemetryEmitter(Protocol):
    """Anything callable ``(event, payload)`` satisfies this."""

    def emit(self, event: str, payload: dict[str, Any]) -> None: ...


class NullEmitter:
    """Default — swallows events. Zero overhead."""

    def emit(self, event: str, payload: dict[str, Any]) -> None:
        return


class StdoutEmitter:
    """JSON-lines emitter to stdout (or any file handle).

    Useful for CLI smoke tests, shell pipelines, and when sibling tools
    like ``jq`` should slice the event stream.
    """

    def __init__(self, stream: Any = sys.stdout) -> None:
        self._stream = stream

    def emit(self, event: str, payload: dict[str, Any]) -> None:
        line = json.dumps({"event": event, **payload}, default=str)
        self._stream.write(line + "\n")
        self._stream.flush()


class ListEmitter:
    """In-memory emitter — captures events for tests + replay."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(self, event: str, payload: dict[str, Any]) -> None:
        self.events.append((event, dict(payload)))


# ---------------------------------------------------------------------------
# Process-wide default-emitter resolution
#
# ``LearnedSwitch.__init__`` calls :func:`get_default_emitter` when no
# emitter is supplied. Integrations (e.g. the hosted cloud pipe) can
# install themselves by calling :func:`register_default_emitter` once
# on import. The hook is process-global by design — the cloud pipe is
# a per-installation decision (logged in vs not), not a per-switch one.
# ---------------------------------------------------------------------------


_NO_TELEMETRY_ENV = "POSTRULE_NO_TELEMETRY"


def _default_factory_noop() -> TelemetryEmitter:
    return NullEmitter()


_default_factory: Callable[[], TelemetryEmitter] = _default_factory_noop
_default_factory_lock = threading.Lock()


def register_default_emitter(factory: Callable[[], TelemetryEmitter]) -> None:
    """Install a process-wide default-emitter factory.

    The factory is called once per :class:`~postrule.core.LearnedSwitch`
    constructed without an explicit ``telemetry=`` argument. Repeated
    calls overwrite the previous factory; this keeps the registration
    contract simple for the common case (one cloud bridge per process).

    Pass :func:`reset_default_emitter` (or call this with a factory that
    returns :class:`NullEmitter`) to opt back out at runtime.
    """
    global _default_factory
    with _default_factory_lock:
        _default_factory = factory


def reset_default_emitter() -> None:
    """Reset the default-emitter factory to the built-in NullEmitter."""
    global _default_factory
    with _default_factory_lock:
        _default_factory = _default_factory_noop


def get_default_emitter() -> TelemetryEmitter:
    """Return the process-wide default emitter for a fresh switch.

    Honors ``$POSTRULE_NO_TELEMETRY``: when set to a truthy value, returns
    :class:`NullEmitter` regardless of which factory is registered. This
    is the operator-side opt-out and supersedes any registered bridge.

    Failure inside the registered factory is treated as opt-out — the
    integration is best-effort and must never break switch construction.
    """
    if _telemetry_disabled_by_env():
        return NullEmitter()
    with _default_factory_lock:
        factory = _default_factory
    try:
        emitter = factory()
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return NullEmitter()
    if emitter is None:
        return NullEmitter()
    return emitter


def _telemetry_disabled_by_env() -> bool:
    """Return True iff ``$POSTRULE_NO_TELEMETRY`` is set to a truthy value.

    Truthy = non-empty and not in the canonical "false" set. We accept
    the usual suspects so CI configs don't get tripped up by case.
    """
    raw = os.environ.get(_NO_TELEMETRY_ENV)
    if raw is None:
        return False
    return raw.strip().lower() not in ("", "0", "false", "no", "off")


__all__ = [
    "ListEmitter",
    "NullEmitter",
    "StdoutEmitter",
    "TelemetryEmitter",
    "get_default_emitter",
    "register_default_emitter",
    "reset_default_emitter",
]
