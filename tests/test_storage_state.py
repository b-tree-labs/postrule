# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Per-switch state interface on Storage backends.

Beyond the verdict log, a switch holds small per-switch state — the trained
ML head, the circuit-breaker flag, and (post-#60) the signal signature +
audit ledger. Routing these through `Storage.put_state/get_state/
delete_state` is what lets the data plane live in a shared / managed backend
for elastic compute (docs/design/state-and-deployment-architecture.md).

These tests pin: (1) every built-in backend round-trips state; (2)
FileStorage co-locates state with the log using the legacy `.head`/`.breaker`
filenames (so existing persisted state survives the upgrade); (3) the switch
persists + rehydrates head/breaker THROUGH the storage backend; (4) a custom
Storage that doesn't implement the state methods falls back to local files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from postrule.storage import (
    BoundedInMemoryStorage,
    FileStorage,
    InMemoryStorage,
    ResilientStorage,
    SqliteStorage,
)


def _backends(tmp_path: Path):
    return [
        InMemoryStorage(),
        BoundedInMemoryStorage(),
        FileStorage(str(tmp_path / "fs")),
        SqliteStorage(str(tmp_path / "db.sqlite")),
        ResilientStorage(FileStorage(str(tmp_path / "rfs"))),
    ]


class TestStateRoundTrip:
    def test_every_backend_round_trips(self, tmp_path: Path) -> None:
        for s in _backends(tmp_path):
            assert s.get_state("sw", "head") is None, type(s).__name__
            s.put_state("sw", "head", b"\x00\x01blob")
            assert s.get_state("sw", "head") == b"\x00\x01blob", type(s).__name__
            # overwrite
            s.put_state("sw", "head", b"new")
            assert s.get_state("sw", "head") == b"new", type(s).__name__
            # isolation by (switch, key)
            assert s.get_state("other", "head") is None
            assert s.get_state("sw", "breaker") is None
            # delete
            s.delete_state("sw", "head")
            assert s.get_state("sw", "head") is None, type(s).__name__
            # delete of absent key is a no-op
            s.delete_state("sw", "head")


class TestFileStorageLegacyPaths:
    def test_head_breaker_use_legacy_filenames(self, tmp_path: Path) -> None:
        # Existing persisted `.head`/`.breaker` files must still be found.
        base = tmp_path / "fs"
        s = FileStorage(str(base))
        s.put_state("grader", "head", b"H")
        s.put_state("grader", "breaker", b"1")
        s.put_state("grader", "signature", b"{}")
        assert (base / "grader" / ".head").read_bytes() == b"H"
        assert (base / "grader" / ".breaker").read_bytes() == b"1"
        # non-legacy keys get a namespaced filename
        assert (base / "grader" / ".state-signature").read_bytes() == b"{}"


class TestSwitchPersistsThroughStorage:
    def test_breaker_survives_via_storage_backend(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # persist=True uses the default ResilientStorage(FileStorage), which is
        # state-capable — so breaker state now routes through the storage state
        # interface (not a hardcoded path). Sandbox cwd so runtime/ is isolated.
        from postrule import LearnedSwitch, Phase

        monkeypatch.chdir(tmp_path)

        def rule(_x):
            return "a"

        s1 = LearnedSwitch(rule=rule, name="brk", starting_phase=Phase.RULE, persist=True)
        assert s1._state_capable() is True
        s1._circuit_tripped = True
        s1._save_breaker_state()
        # Landed in the switch's storage backend via the state interface.
        assert s1._storage.get_state("brk", "breaker") == b"1"

        # A fresh switch (same default backend + cwd) rehydrates the trip.
        s2 = LearnedSwitch(rule=rule, name="brk", starting_phase=Phase.RULE, persist=True)
        assert s2._circuit_tripped is True


class TestFallbackForNonStateBackend:
    def test_put_state_falls_back_to_local_when_not_state_capable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # If the configured backend lacks the state interface (older custom
        # Storage), state routes to the legacy local sidecar file — identical
        # to pre-upgrade behavior. Forward-looking: reachable once persist+
        # custom-storage is allowed; pinned here at the helper level.
        from postrule import LearnedSwitch, Phase

        monkeypatch.chdir(tmp_path)

        def rule(_x):
            return "a"

        class BareStorage:  # protocol-only: NO state methods
            def append_record(self, name, rec): ...
            def load_records(self, name):
                return []

        s = LearnedSwitch(rule=rule, name="bare", starting_phase=Phase.RULE, persist=True)
        s._storage = BareStorage()  # simulate an older custom backend
        assert s._state_capable() is False
        s._put_state("breaker", b"1")
        assert (tmp_path / "runtime" / "postrule" / "bare" / ".breaker").read_bytes() == b"1"
        assert s._get_state("breaker") == b"1"
        s._delete_state("breaker")
        assert s._get_state("breaker") is None
