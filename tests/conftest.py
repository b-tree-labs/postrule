# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Sandbox harness for the Postrule test suite.

This conftest installs three autouse guards so that tests cannot
escape into the developer's real local environment. The guards are
the foundation that the chaos / red-team / perf suites are built
on, but they apply to every Python test pytest collects under
``tests/``.

Default guarantees (all on by default):

- ``HOME`` / ``USERPROFILE`` / ``XDG_*`` are redirected to
  ``tmp_path`` and ``Path.home()`` returns ``tmp_path``.
- Outbound socket connections that are not loopback raise
  ``RuntimeError`` from the guard. Loopback (``127.0.0.1`` / ``::1``)
  and unix-socket paths are passed through.
- Disk writes via ``Path.write_text`` / ``Path.write_bytes`` /
  ``Path.open`` (write modes) / ``builtins.open`` (write modes)
  raise if the resolved path lies outside ``tmp_path``. Writes to
  ``tmp_path/**``, the system temp dir, ``/dev/null``, and
  in-memory file-like objects (``io.StringIO`` / ``io.BytesIO``)
  pass through.

Three opt-in fixtures let an individual test relax a single guard:
``network_enabled``, ``home_writable``, ``external_writes_allowed``.
See ``tests/README.md`` for usage notes.
"""

from __future__ import annotations

import builtins
import io
import os
import socket
import tempfile
from pathlib import Path

import pytest

# Real callables we will dispatch to when a write is allowed. Captured
# at import time so later monkeypatches don't recurse.
_REAL_OPEN = builtins.open
_REAL_PATH_OPEN = Path.open
_REAL_PATH_WRITE_TEXT = Path.write_text
_REAL_PATH_WRITE_BYTES = Path.write_bytes
_REAL_SOCKET_CONNECT = socket.socket.connect

# System-level temp directories that the OS hands out via
# ``tempfile.gettempdir`` and friends. Tests that go through
# ``tempfile.TemporaryDirectory`` should keep working without an
# opt-in, since the path is still local and process-bound.
_SYSTEM_TMP_DIRS: tuple[Path, ...] = tuple(
    {
        Path(tempfile.gettempdir()).resolve(),
        Path("/tmp").resolve() if Path("/tmp").exists() else Path(tempfile.gettempdir()).resolve(),
        Path("/private/tmp").resolve()
        if Path("/private/tmp").exists()
        else Path(tempfile.gettempdir()).resolve(),
        Path("/var/folders").resolve()
        if Path("/var/folders").exists()
        else Path(tempfile.gettempdir()).resolve(),
        Path("/private/var/folders").resolve()
        if Path("/private/var/folders").exists()
        else Path(tempfile.gettempdir()).resolve(),
    }
)

# Modes that imply the open call may write to disk. Anything with
# ``w``, ``a``, ``x``, or ``+`` qualifies. ``r`` and ``rb`` without
# ``+`` are read-only and pass through.
_WRITE_MODE_CHARS = frozenset("wax+")


def _is_write_mode(mode: object) -> bool:
    if not isinstance(mode, str):
        return False
    return any(ch in _WRITE_MODE_CHARS for ch in mode)


def _path_under(child: Path, parent: Path) -> bool:
    """Return True if ``child`` is ``parent`` or a descendant of it.

    Uses ``os.path.commonpath`` against resolved (or absolute, when
    the path does not yet exist) representations so we don't get
    fooled by ``..`` segments or symlink hops.
    """
    try:
        c = child.resolve()
    except (OSError, RuntimeError):
        c = Path(os.path.abspath(str(child)))
    try:
        p = parent.resolve()
    except (OSError, RuntimeError):
        p = Path(os.path.abspath(str(parent)))
    try:
        common = Path(os.path.commonpath([str(c), str(p)]))
    except ValueError:
        return False
    return common == p


def _is_allowed_write_target(path_like: object, tmp_root: Path) -> bool:
    """Decide whether a write to ``path_like`` should be permitted.

    Allow:

    - in-memory targets (``int`` file descriptors, ``io.StringIO`` /
      ``io.BytesIO`` instances, anything that is not a path-like
      object),
    - ``/dev/null``,
    - paths inside ``tmp_root`` (the test's ``tmp_path``),
    - paths inside the OS temp dirs ``tempfile`` hands out,
    - paths inside the repo's pytest cache and coverage data files
      (so the test runner itself can keep functioning).
    """
    # File descriptors and non-path-like objects (e.g. StringIO) are
    # not disk writes. Pass through.
    if isinstance(path_like, int):
        return True
    if isinstance(path_like, (io.IOBase,)):
        return True
    if not isinstance(path_like, (str, bytes, os.PathLike)):
        return True
    try:
        candidate = Path(os.fsdecode(path_like))
    except (TypeError, ValueError):
        return True

    # /dev/null is always safe.
    if str(candidate) == "/dev/null":
        return True

    if _path_under(candidate, tmp_root):
        return True

    return any(_path_under(candidate, tmp_dir) for tmp_dir in _SYSTEM_TMP_DIRS)


def _format_block_message(path_like: object, tmp_root: Path) -> str:
    return (
        "external write blocked in sandbox: "
        f"{path_like!r} is outside tmp_path ({tmp_root}). "
        "Use the external_writes_allowed fixture to opt out."
    )


# ---------------------------------------------------------------------------
# Opt-in fixtures
# ---------------------------------------------------------------------------
#
# These are no-ops on their own. The autouse guards below check
# ``request.fixturenames`` to decide whether to install the matching
# guard for a given test. That keeps the opt-in surface tiny: just
# add the fixture name to the test signature.


@pytest.fixture
def network_enabled():
    """Opt out of the outbound-network guard for this test.

    The guard is suppressed for the duration of the test, but the
    actual network call still has to succeed on its own merits. CI
    runners with no egress will still see ``ECONNREFUSED`` /
    ``OSError``; the guard simply will not raise its own
    ``RuntimeError`` on top.
    """
    return True


@pytest.fixture
def home_writable():
    """Opt out of the HOME redirect for this test.

    After this fixture runs, ``Path.home()`` and the ``HOME`` /
    ``XDG_*`` env vars point at the developer's real home directory.
    Use this only for tests that genuinely need to read user
    configuration; never for the chaos / red-team / perf suites.
    """
    return True


@pytest.fixture
def external_writes_allowed():
    """Opt out of the external-write guard for this test.

    Use sparingly: most tests that think they need this actually
    want ``tmp_path``. Reserved for tests that exercise file I/O
    against well-known locations (e.g. ``/dev/shm`` perf probes)
    that the harness cannot anticipate.
    """
    return True


# ---------------------------------------------------------------------------
# Autouse guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _sandbox_home(request, tmp_path, monkeypatch):
    """Redirect HOME / XDG_* / Path.home() to ``tmp_path``.

    Suppressed when the test signature requests
    ``home_writable``.
    """
    if "home_writable" in request.fixturenames:
        return
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / ".cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / ".local" / "share"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Disable the cohort-defaults async refresh during tests.
    # The fetch fires when `postrule analyze` runs; without this guard
    # the daemon thread tries to reach postrule.ai, the sandbox blocks
    # the connect, and the unraised exception leaks into pytest as
    # a PytestUnraisableExceptionWarning. The CLI smoke tests don't
    # care about cohort defaults; opt out unconditionally.
    monkeypatch.setenv("POSTRULE_NO_INSIGHTS_FETCH", "1")


@pytest.fixture(autouse=True, scope="session")
def _block_outbound_network():
    """Block non-loopback outbound socket connects for the session.

    Loopback (``127.0.0.1`` / ``::1``) and unix-socket paths are
    allowed so in-process tests using a local HTTP server keep
    working. Suppression for individual tests happens inside the
    guard itself by inspecting the active request (see
    ``_per_test_network_gate`` below).
    """
    real_connect = _REAL_SOCKET_CONNECT
    state: dict[str, bool] = {"enabled": True}

    def guarded_connect(self, address, *args, **kwargs):
        if not state["enabled"]:
            return real_connect(self, address, *args, **kwargs)
        if _is_loopback_target(self, address):
            return real_connect(self, address, *args, **kwargs)
        raise RuntimeError(
            "network access blocked in sandbox; "
            "use the network_enabled fixture to opt in. "
            f"target={address!r} family={getattr(self, 'family', None)}"
        )

    socket.socket.connect = guarded_connect
    # Stash on the module for the per-test gate to flip.
    _GUARD_STATE["network"] = state
    try:
        yield
    finally:
        socket.socket.connect = real_connect


_GUARD_STATE: dict[str, dict[str, bool]] = {}


def _is_loopback_target(sock: socket.socket, address: object) -> bool:
    family = getattr(sock, "family", None)
    if family == socket.AF_UNIX:
        return True
    if isinstance(address, tuple) and address:
        host = address[0]
        if isinstance(host, str) and host in {"127.0.0.1", "::1", "localhost", ""}:
            return True
        if isinstance(host, str) and host.startswith("127."):
            return True
    # AF_UNIX path-style addresses are bare strings.
    return isinstance(address, str)


@pytest.fixture(autouse=True)
def _per_test_network_gate(request):
    """Per-test toggle for the session-level network guard.

    If a test asks for ``network_enabled``, flip the guard off for
    the duration of the test, then restore it.
    """
    state = _GUARD_STATE.get("network")
    if state is None:
        # Session fixture has not run yet (e.g. collection-only).
        yield
        return
    if "network_enabled" in request.fixturenames:
        previous = state["enabled"]
        state["enabled"] = False
        try:
            yield
        finally:
            state["enabled"] = previous
    else:
        yield


@pytest.fixture(autouse=True)
def _block_external_writes(request, tmp_path, monkeypatch):
    """Raise on writes that resolve outside ``tmp_path``.

    Suppressed when the test signature requests
    ``external_writes_allowed``.
    """
    if "external_writes_allowed" in request.fixturenames:
        return

    real_open = _REAL_OPEN
    real_path_open = _REAL_PATH_OPEN
    real_write_text = _REAL_PATH_WRITE_TEXT
    real_write_bytes = _REAL_PATH_WRITE_BYTES

    def guarded_builtin_open(file, mode="r", *args, **kwargs):
        if _is_write_mode(mode) and not _is_allowed_write_target(file, tmp_path):
            raise RuntimeError(_format_block_message(file, tmp_path))
        return real_open(file, mode, *args, **kwargs)

    def guarded_path_open(self, mode="r", *args, **kwargs):
        if _is_write_mode(mode) and not _is_allowed_write_target(self, tmp_path):
            raise RuntimeError(_format_block_message(self, tmp_path))
        return real_path_open(self, mode, *args, **kwargs)

    def guarded_write_text(self, *args, **kwargs):
        if not _is_allowed_write_target(self, tmp_path):
            raise RuntimeError(_format_block_message(self, tmp_path))
        return real_write_text(self, *args, **kwargs)

    def guarded_write_bytes(self, *args, **kwargs):
        if not _is_allowed_write_target(self, tmp_path):
            raise RuntimeError(_format_block_message(self, tmp_path))
        return real_write_bytes(self, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_builtin_open)
    monkeypatch.setattr(Path, "open", guarded_path_open)
    monkeypatch.setattr(Path, "write_text", guarded_write_text)
    monkeypatch.setattr(Path, "write_bytes", guarded_write_bytes)
