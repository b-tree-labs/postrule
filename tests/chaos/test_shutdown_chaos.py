# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Shutdown chaos: KeyboardInterrupt mid-dispatch, SIGTERM during a
batched flush, daemon threads at shutdown, atexit ordering.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from dendra import (
    BoundedInMemoryStorage,
    ClassificationRecord,
    FileStorage,
    LearnedSwitch,
    Phase,
    SwitchConfig,
    Verdict,
)


def _rec(label: str = "x") -> ClassificationRecord:
    return ClassificationRecord(
        timestamp=time.time(),
        input="i",
        label=label,
        outcome=Verdict.CORRECT.value,
        source="rule",
        confidence=1.0,
    )


# ---------------------------------------------------------------------------
# KeyboardInterrupt mid-dispatch
# ---------------------------------------------------------------------------


class TestKeyboardInterruptMidDispatch:
    def test_kbd_interrupt_in_action_propagates(self):
        """KeyboardInterrupt from inside an on= handler must propagate cleanly.

        Contract from core.py:_maybe_dispatch: KeyboardInterrupt /
        SystemExit are explicitly re-raised regardless of the
        propagate_action_exceptions setting. The classify decision is
        already locked in at this point, but the action is interrupted.
        """

        def angry_handler(_):
            raise KeyboardInterrupt("user pressed C-c")

        sw = LearnedSwitch(
            rule=lambda x: "go",
            name="kbd_chaos",
            author="t",
            labels={"go": angry_handler},
            config=SwitchConfig(
                starting_phase=Phase.RULE,
                auto_record=False,
                auto_advance=False,
                auto_demote=False,
            ),
            storage=BoundedInMemoryStorage(),
        )
        with pytest.raises(KeyboardInterrupt):
            sw.dispatch("input")

    def test_kbd_interrupt_during_classify_impl(self):
        """KeyboardInterrupt from the rule body propagates."""

        def angry_rule(_):
            raise KeyboardInterrupt("user pressed C-c")

        sw = LearnedSwitch(
            rule=angry_rule,
            name="kbd_in_rule",
            author="t",
            config=SwitchConfig(
                starting_phase=Phase.RULE,
                auto_record=False,
                auto_advance=False,
                auto_demote=False,
            ),
            storage=BoundedInMemoryStorage(),
        )
        with pytest.raises(KeyboardInterrupt):
            sw.classify("input")


# ---------------------------------------------------------------------------
# Batched flush at shutdown
# ---------------------------------------------------------------------------


class TestBatchedFlushAtShutdown:
    def test_close_drains_pending_writes(self, tmp_path):
        """close() blocks until pending records are durable.

        Writes go into the queue, close() waits for the flusher thread
        to drain. Reopen + load_records must see them all.
        """
        store = FileStorage(
            tmp_path / "store", batching=True, batch_size=1024, flush_interval_ms=1000
        )
        for i in range(50):
            store.append_record("s", _rec(f"r{i}"))
        store.close()

        # Re-open: every record must have made it.
        store2 = FileStorage(tmp_path / "store")
        labels = sorted(r.label for r in store2.load_records("s"))
        assert labels == sorted(f"r{i}" for i in range(50)), "close() did not drain queue"

    def test_close_is_idempotent(self, tmp_path):
        """Calling close() multiple times must not error or hang."""
        store = FileStorage(tmp_path / "store", batching=True)
        store.append_record("s", _rec())
        store.close()
        store.close()  # second call must be a no-op
        store.close()

    def test_append_after_close_raises(self, tmp_path):
        """append_record after close() must raise , silent loss is the bug."""
        store = FileStorage(tmp_path / "store", batching=True)
        store.close()
        with pytest.raises(RuntimeError):
            store.append_record("s", _rec())


# ---------------------------------------------------------------------------
# Daemon-thread join deadline
# ---------------------------------------------------------------------------


class TestFlusherJoinDeadline:
    def test_close_returns_within_deadline(self, tmp_path):
        """close() must join the flusher within its own 2s deadline.

        Even if the flusher is mid-write, close() walks back within
        ~2 seconds. A flusher that hangs forever blocks shutdown.
        """
        store = FileStorage(tmp_path / "store", batching=True, batch_size=2, flush_interval_ms=10)
        for i in range(20):
            store.append_record("s", _rec(f"r{i}"))

        t0 = time.monotonic()
        store.close()
        elapsed = time.monotonic() - t0
        # The configured deadline is 2.0s. Real-world it should be
        # well under that. Allow 3s for slow CI.
        assert elapsed < 3.0, f"close() took {elapsed:.2f}s; flusher hung"


# ---------------------------------------------------------------------------
# atexit ordering
# ---------------------------------------------------------------------------


class TestAtexitDurability:
    @pytest.mark.slow
    def test_atexit_drains_batched_storage(self, tmp_path):
        """A process that doesn't call close() explicitly: atexit must drain.

        Run a child process that opens a batched FileStorage, writes 20
        records, and exits without calling close(). Reopen from the
        parent and confirm the records made it.

        Subprocess sandboxing note: tests/conftest.py only protects the
        parent process. The child uses the parent's tmp_path explicitly,
        so it stays inside the sandbox by construction.
        """
        base = tmp_path / "store"
        script = (
            "import sys\n"
            f"sys.path.insert(0, {str(Path(__file__).parents[2] / 'src')!r})\n"
            "from dendra import FileStorage, ClassificationRecord, Verdict\n"
            "import time\n"
            f"store = FileStorage({str(base)!r}, batching=True, batch_size=128, flush_interval_ms=10000)\n"
            "for i in range(20):\n"
            "    store.append_record('s', ClassificationRecord(\n"
            "        timestamp=time.time(), input='i', label=f'r{i}',\n"
            "        outcome=Verdict.CORRECT.value, source='rule', confidence=1.0))\n"
            # No explicit close; rely on atexit.
        )
        # Subprocess: avoid network, point home at tmp_path. The child
        # never imports tests/conftest.py.
        env = dict(os.environ)
        env["HOME"] = str(tmp_path)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        assert result.returncode == 0, f"child failed: {result.stderr}"

        # Parent reads the same store.
        store = FileStorage(base)
        labels = sorted(r.label for r in store.load_records("s"))
        assert labels == sorted(f"r{i}" for i in range(20)), (
            f"atexit did not drain queue; got {labels}"
        )


# ---------------------------------------------------------------------------
# SystemExit during dispatch
# ---------------------------------------------------------------------------


class TestSystemExitMidDispatch:
    def test_system_exit_in_handler_propagates(self):
        """SystemExit from inside an on= handler propagates."""

        def quitter(_):
            raise SystemExit("graceful")

        sw = LearnedSwitch(
            rule=lambda x: "go",
            name="sysexit_chaos",
            author="t",
            labels={"go": quitter},
            config=SwitchConfig(
                starting_phase=Phase.RULE,
                auto_record=False,
                auto_advance=False,
                auto_demote=False,
            ),
            storage=BoundedInMemoryStorage(),
        )
        with pytest.raises(SystemExit):
            sw.dispatch("input")
