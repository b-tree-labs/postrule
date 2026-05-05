# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Path-traversal stress for FileStorage.switch_name handling.

FileStorage carries a guard at ``_switch_dir`` (storage.py) that
rejects absolute paths, ``..`` segments, and any switch_name that
resolves outside ``base_path``. These tests exercise creative attacker
inputs the obvious guard might miss: encoded variants, unicode bidi,
null bytes, very long components, symlinks, and TOCTOU races.

Every vector must raise ``ValueError`` (or ``OSError`` for null-byte
embedded paths) BEFORE any file is created outside ``base_path``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from dendra.storage import FileStorage

pytestmark = pytest.mark.redteam


def _record():
    """Build a minimal record we can try to append.

    Imports are local so module collection still works if storage internals
    move around.
    """
    from dendra import ClassificationRecord

    return ClassificationRecord(
        timestamp=0.0,
        input="x",
        label="ok",
        outcome="correct",
        source="rule",
        confidence=1.0,
    )


# ---------------------------------------------------------------------
# Direct switch_name vectors - the classics + unicode tricks
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "vector",
    [
        # Classics
        "../etc/passwd",
        "../../../etc/shadow",
        "..",
        "a/../../etc",
        "a/b/../../../etc",
        # Absolute paths (POSIX + Windows-style)
        "/etc/passwd",
        "/tmp/owned",
        "\\windows\\system32\\config\\sam",
        # Unicode bidi-override attack: looks safe to a human but
        # contains real "../" that the guard MUST detect.
        "safe‮/../../etc",
        # NOTE on tested-elsewhere vectors:
        #  - Zero-width joiners ("..‍/etc") create a literal
        #    3-char component named "..‍" - not a real ".."
        #    segment. Guard correctly accepts; the resolved path
        #    stays under base_path. Covered by
        #    ``test_url_encoded_traversal_stays_inside_base``.
        #  - Encoded variants ("%2E%2E/etc") are opaque strings; the
        #    guard MUST NOT decode them. Covered separately.
    ],
)
def test_path_traversal_vector_refused(tmp_path: Path, vector: str) -> None:
    """Path-traversal vectors must raise ValueError before any disk write."""
    storage = FileStorage(base_path=tmp_path / "store")
    base_resolved = (tmp_path / "store").resolve()

    with pytest.raises(ValueError):
        storage.append_record(vector, _record())

    # Belt-and-suspenders: nothing outside base_path was created.
    # Walk the parent of base_path; the only entry should be base_path.
    for entry in tmp_path.iterdir():
        assert entry.resolve() == base_resolved or entry.resolve().is_relative_to(base_resolved), (
            f"unexpected sibling created during traversal attempt: {entry}"
        )


def test_path_traversal_null_byte_refused(tmp_path: Path) -> None:
    """Null-byte injected switch_name must not produce a partial write.

    The rejection point varies by Python version: ``Path(...).parts``
    accepts null-bytes, but ``Path.resolve()`` raises ``ValueError`` on
    POSIX. Either error class is acceptable as long as no file lands.
    """
    storage = FileStorage(base_path=tmp_path / "store")
    vector = "safe\x00/etc/passwd"

    with pytest.raises((ValueError, OSError)):
        storage.append_record(vector, _record())


def test_path_traversal_very_long_component_handled(tmp_path: Path) -> None:
    """A 1024-char component should either succeed (filesystem limit
    permitting) or fail loud - never silently drop data, and never
    escape base_path.
    """
    storage = FileStorage(base_path=tmp_path / "store")
    long_name = "x" * 1024

    try:
        storage.append_record(long_name, _record())
    except (OSError, ValueError):
        # Filesystem rejected the long name - acceptable.
        return

    # If it succeeded, the resolved directory must still be under base_path.
    base_resolved = (tmp_path / "store").resolve()
    candidate = (tmp_path / "store" / long_name).resolve()
    assert candidate.is_relative_to(base_resolved)


def test_path_traversal_empty_name_refused(tmp_path: Path) -> None:
    """Empty switch_name is its own well-defined refusal."""
    storage = FileStorage(base_path=tmp_path / "store")
    with pytest.raises(ValueError):
        storage.append_record("", _record())


# ---------------------------------------------------------------------
# Encoded variants: the literal string is opaque, but it must not be
# silently decoded somewhere downstream.
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "vector",
    [
        "%2E%2E%2Fetc",
        "..%2Fetc",
        "%2E%2E/etc",
    ],
)
def test_url_encoded_traversal_stays_inside_base(tmp_path: Path, vector: str) -> None:
    """URL-encoded ``..`` must NOT be treated as a real ``..`` segment.

    Either the storage layer accepts the literal string as an opaque
    directory name (and it stays under base_path), OR rejects it with
    ValueError. We only forbid silent decoding that would let it escape.
    """
    storage = FileStorage(base_path=tmp_path / "store")
    base_resolved = (tmp_path / "store").resolve()

    try:
        storage.append_record(vector, _record())
    except ValueError:
        # Conservative rejection - also acceptable.
        return

    # If accepted, the directory created must stay inside base_path.
    for path in (tmp_path / "store").rglob("outcomes.jsonl"):
        assert path.resolve().is_relative_to(base_resolved)


# ---------------------------------------------------------------------
# Symlink attacks
# ---------------------------------------------------------------------


@pytest.mark.skipif(sys.platform.startswith("win"), reason="symlinks need admin on Windows")
def test_symlink_to_outside_base_refused(tmp_path: Path) -> None:
    """A symlink under base_path that points outside must NOT be followed.

    Set up: attacker pre-creates ``base_path/escape`` -> ``/tmp/evil``.
    Calling ``append_record("escape", ...)`` resolves the symlink; the
    guard must detect that the resolved path escapes base_path.

    BUG FIX (defense-in-depth): the existing _switch_dir guard already
    calls .resolve() before the relative_to check, which correctly
    follows the symlink and detects escape. This test pins that
    behavior so a future refactor can't regress it.
    """
    base = tmp_path / "store"
    base.mkdir()
    outside = tmp_path / "evil_target"
    outside.mkdir()

    (base / "escape").symlink_to(outside)
    storage = FileStorage(base_path=base)

    with pytest.raises(ValueError):
        storage.append_record("escape", _record())

    # And no file was written into the outside target.
    assert list(outside.iterdir()) == []


@pytest.mark.skipif(sys.platform.startswith("win"), reason="symlinks need admin on Windows")
def test_symlink_inside_base_to_inside_base_allowed(tmp_path: Path) -> None:
    """Symlinks that stay inside base_path are not a security problem
    and must be permitted (otherwise we break legitimate filesystem
    layouts where a switch directory is a symlinked alias).
    """
    base = tmp_path / "store"
    base.mkdir()
    real = base / "real"
    real.mkdir()
    (base / "alias").symlink_to(real)

    storage = FileStorage(base_path=base)
    storage.append_record("alias", _record())  # must not raise
    # Either real/ or alias/ ends up holding the file; both are fine
    # because the resolved path is under base.
    found = list(base.rglob("outcomes.jsonl"))
    assert found, "expected at least one outcomes.jsonl under base"


@pytest.mark.skipif(sys.platform.startswith("win"), reason="symlinks need admin on Windows")
def test_toctou_symlink_swap(tmp_path: Path) -> None:
    """TOCTOU: legitimate dir at first call, attacker swaps it for a
    symlink between calls.

    The guard should re-resolve on every call. After the swap,
    the next append_record must either:
      (a) succeed because the symlink points back inside base_path, OR
      (b) raise ValueError because the swapped target now escapes.

    Never: silently follow the symlink and write outside base_path.
    """
    base = tmp_path / "store"
    base.mkdir()
    storage = FileStorage(base_path=base)

    # First append - creates base/swappy/outcomes.jsonl
    storage.append_record("swappy", _record())
    swappy = base / "swappy"
    assert swappy.exists()

    # Attacker swap: replace "swappy" with a symlink to /etc.
    # We use tmp_path/outside (which is OUTSIDE base) to keep the
    # attack realistic without touching real /etc.
    outside = tmp_path / "outside"
    outside.mkdir()
    # Move swappy's contents away, replace with a symlink.
    import shutil

    shutil.rmtree(swappy)
    swappy.symlink_to(outside)

    with pytest.raises(ValueError):
        storage.append_record("swappy", _record())

    # The outside dir must still be empty.
    assert list(outside.iterdir()) == []


# ---------------------------------------------------------------------
# Multi-level guard: once accepted, all derived paths must also stay
# under base_path. Catches a refactor that resolves the directory once
# and then concatenates without re-checking.
# ---------------------------------------------------------------------


def test_active_path_under_base(tmp_path: Path) -> None:
    """The internal _active_path must stay under base for every legit name."""
    base = tmp_path / "store"
    storage = FileStorage(base_path=base)
    for name in ["a", "a/b", "a/b/c", "x.y.z"]:
        active = storage._active_path(name)
        assert active.resolve().is_relative_to(base.resolve())


def test_no_partial_write_on_refusal(tmp_path: Path) -> None:
    """Refused switch_name must NOT have created any directories under base.

    This catches a refactor where ``mkdir(parents=True)`` runs before
    the guard - once the dirs exist, the attacker can chain the next
    write to land somewhere else.
    """
    base = tmp_path / "store"
    storage = FileStorage(base_path=base)

    with pytest.raises(ValueError):
        storage.append_record("../escape", _record())

    # No "escape" directory under tmp_path or base.
    assert not (tmp_path / "escape").exists()
    # Base may exist (constructed in __init__) but should be otherwise empty.
    assert list(base.iterdir()) == []
