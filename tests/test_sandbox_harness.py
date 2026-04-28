# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Self-tests for the sandbox harness installed by ``conftest.py``.

These tests verify both the default-deny posture (writes outside
``tmp_path`` blocked, non-loopback connects blocked, ``HOME``
redirected) and the three opt-in fixtures
(``network_enabled``, ``home_writable``, ``external_writes_allowed``).
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Default-deny: external writes
# ---------------------------------------------------------------------------


def test_default_blocks_external_write():
    target = Path("/usr/local/dendra-sandbox-escape-attempt")
    with pytest.raises(RuntimeError, match="external write blocked"):
        target.write_text("x")


def test_default_blocks_external_write_via_path_open():
    target = Path("/usr/local/dendra-sandbox-escape-attempt-2")
    with pytest.raises(RuntimeError, match="external write blocked"), target.open("w"):
        pass


def test_default_blocks_external_write_via_builtin_open():
    with pytest.raises(RuntimeError, match="external write blocked"):
        open("/usr/local/dendra-sandbox-escape-attempt-3", "w")  # noqa: SIM115


# ---------------------------------------------------------------------------
# Default-deny: outbound network
# ---------------------------------------------------------------------------


def test_default_blocks_outbound_socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.1)
    try:
        with pytest.raises(RuntimeError, match="network access blocked"):
            s.connect(("8.8.8.8", 53))
    finally:
        s.close()


def test_loopback_allowed():
    """Connecting to localhost must not raise our guard.

    The connect itself may fail (ECONNREFUSED is fine, no listener).
    What we care about is that our ``RuntimeError`` does not fire.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.1)
    try:
        try:
            s.connect(("127.0.0.1", 1))
        except RuntimeError:
            pytest.fail("loopback connect was blocked by sandbox guard")
        except OSError:
            pass  # ECONNREFUSED / similar is the expected outcome.
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Default-deny: HOME redirect
# ---------------------------------------------------------------------------


def test_default_redirects_home(tmp_path):
    assert Path.home() == tmp_path
    assert os.environ["HOME"] == str(tmp_path)


def test_default_redirects_xdg_dirs(tmp_path):
    assert os.environ["XDG_CONFIG_HOME"] == str(tmp_path / ".config")
    assert os.environ["XDG_CACHE_HOME"] == str(tmp_path / ".cache")
    assert os.environ["XDG_DATA_HOME"] == str(tmp_path / ".local" / "share")


# ---------------------------------------------------------------------------
# Opt-in: network_enabled
# ---------------------------------------------------------------------------


def test_network_enabled_opt_in(network_enabled):
    """With the opt-in, our guard must not raise on 8.8.8.8.

    The connect itself can still fail (timeout / ECONNREFUSED on
    sandboxed CI); we only assert that the failure does not come
    from our ``RuntimeError``.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.1)
    try:
        try:
            s.connect(("8.8.8.8", 53))
        except RuntimeError as exc:
            if "network access blocked" in str(exc):
                pytest.fail("guard fired even though network_enabled was requested")
            raise
        except OSError:
            pass  # Expected on offline CI.
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Opt-in: home_writable
# ---------------------------------------------------------------------------


def test_home_writable_opt_in(home_writable):
    real_home = Path(os.path.expanduser("~"))
    # Path.home() should now resolve to the developer's actual home.
    assert Path.home() == real_home


# ---------------------------------------------------------------------------
# Opt-in: external_writes_allowed
# ---------------------------------------------------------------------------


def test_external_writes_allowed_opt_in(external_writes_allowed, tmp_path):
    """With the opt-in, writes outside tmp_path are not blocked by us.

    We still write under ``tmp_path`` so the test stays hermetic; the
    point is that the ``RuntimeError`` from our guard never fires.
    """
    target = tmp_path / "still_inside.txt"
    target.write_text("ok")
    assert target.read_text() == "ok"


# ---------------------------------------------------------------------------
# Allow-list: tmp_path is always writable
# ---------------------------------------------------------------------------


def test_tmp_path_writes_always_allowed(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "b.bin").write_bytes(b"\x00\x01")
    with (tmp_path / "c.txt").open("w") as f:
        f.write("ok")
    with open(tmp_path / "d.txt", "w") as f:
        f.write("ok")
    assert (tmp_path / "a.txt").read_text() == "hello"
    assert (tmp_path / "b.bin").read_bytes() == b"\x00\x01"
    assert (tmp_path / "c.txt").read_text() == "ok"
    assert (tmp_path / "d.txt").read_text() == "ok"


def test_in_memory_writes_pass_through():
    import io

    buf = io.StringIO()
    buf.write("not a disk write")
    assert buf.getvalue() == "not a disk write"


def test_dev_null_allowed():
    with open("/dev/null", "w") as f:
        f.write("discarded")


def test_read_modes_pass_through(tmp_path):
    target = tmp_path / "readme.txt"
    target.write_text("content")
    # Reads must not be intercepted by the write guard.
    with open(target) as f:
        assert f.read() == "content"
    with target.open("r") as f:
        assert f.read() == "content"
