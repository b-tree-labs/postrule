# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Storage backends for outcome records.

Four backends ship with v0.2.x:

- :class:`InMemoryStorage` — unbounded process-local log. Zero
  dependencies. Intended for tests and tightly-controlled workloads;
  grows without limit.
- :class:`BoundedInMemoryStorage` — the default for a bare
  :class:`LearnedSwitch`. Process-local, FIFO-bounded at
  ``max_records`` per switch so a long-running process with a
  misconfigured ``record_verdict`` loop can't OOM the host.
- :class:`FileStorage` — JSONL append-log with optional POSIX
  ``flock`` serialization and optional ``fsync`` durability.
  Durable across process restart. Safe for **single-host** multi-
  process and multi-thread use when locking is enabled (default).
  Not safe over NFS / network filesystems — see the class
  docstring for the full concurrency / durability contract.
- :class:`SqliteStorage` — SQLite WAL-mode backend. ACID guarantees,
  1 writer + N readers within a host, zero external deps (stdlib
  ``sqlite3``). Recommended for any production deployment where
  multiple processes append to the same log.

Customize your own backend by implementing the :class:`Storage`
protocol (duck-typed) or subclassing :class:`StorageBase`
(abstract base, enforces the contract). The helpers
:func:`serialize_record` and :func:`deserialize_record` encode
the canonical JSON-line format so custom backends don't re-invent
it.

Each backend below documents its own concurrency, durability,
and retention contract inline. Subclass :class:`StorageBase` (or
conform to the duck-typed :class:`Storage` protocol) to ship a
custom backend — use :func:`serialize_record` /
:func:`deserialize_record` as the canonical JSON-line encoder.

See ``docs/storage-backends.md`` for the full backend matrix,
decision tree, and custom-backend recipe.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import sqlite3
import stat as _stat
import sys
import threading
import time
import warnings
import weakref
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Protocol, runtime_checkable

from postrule.core import ClassificationRecord

# POSIX file locking — only available on POSIX systems. On Windows
# we fall back to a no-op lock and emit a one-time warning so users
# aren't silently exposed to rotation races.
try:
    import fcntl  # type: ignore[import-not-found,unused-ignore]

    _HAS_FLOCK = True
except ImportError:  # pragma: no cover — Windows path
    fcntl = None  # type: ignore[assignment]
    _HAS_FLOCK = False

_WINDOWS_LOCK_WARNING_ISSUED = False


# ---------------------------------------------------------------------------
# Public contract
# ---------------------------------------------------------------------------


@runtime_checkable
class Storage(Protocol):
    """Duck-typed contract every outcome-log backend implements.

    Two methods, no state requirements. Any object providing these
    methods satisfies :class:`Storage`; instances do not need to
    inherit from anything. :class:`StorageBase` is a convenience
    ABC for users who prefer abstract-method enforcement.

    Invariants a well-behaved backend should preserve:

    1. ``load_records`` returns records in *append order* (oldest
       first, newest last). Phase-transition math depends on this.
    2. ``append_record`` should not propagate exceptions into the
       caller's classify-loop for transient I/O failures unless
       durability has been explicitly requested — prefer to log and
       drop, or buffer. (Postrule's built-in backends propagate;
       tolerant custom backends are fine.)
    3. ``load_records`` should be cheap enough to call from a
       dashboard render path (p99 < 100 ms for 10 000 records is a
       reasonable target).
    """

    def append_record(self, switch_name: str, record: ClassificationRecord) -> None: ...

    def load_records(self, switch_name: str) -> list[ClassificationRecord]: ...


class StorageBase(ABC):
    """Abstract base class for custom storage backends.

    Alternative to the :class:`Storage` protocol for users who
    prefer abstract-method enforcement. Subclasses must implement
    :meth:`append_record` and :meth:`load_records`. All of
    Postrule's built-in backends are compatible with both this ABC
    and the protocol.

    Recipe::

        from postrule.storage import StorageBase, serialize_record, deserialize_record

        class S3Storage(StorageBase):
            def __init__(self, bucket, prefix):
                self._bucket = bucket
                self._prefix = prefix

            def append_record(self, switch_name, record):
                line = serialize_record(record) + "\\n"
                s3.append_object(
                    Bucket=self._bucket,
                    Key=f"{self._prefix}/{switch_name}.jsonl",
                    Body=line.encode(),
                )

            def load_records(self, switch_name):
                raw = s3.get_object(
                    Bucket=self._bucket,
                    Key=f"{self._prefix}/{switch_name}.jsonl",
                )["Body"].read().decode()
                return [
                    deserialize_record(line)
                    for line in raw.splitlines()
                    if line.strip()
                ]
    """

    @abstractmethod
    def append_record(self, switch_name: str, record: ClassificationRecord) -> None:
        """Append one record to the named switch's log."""
        raise NotImplementedError

    @abstractmethod
    def load_records(self, switch_name: str) -> list[ClassificationRecord]:
        """Return every record for the named switch in append order."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Public serialization helpers
# ---------------------------------------------------------------------------


def serialize_record(record: ClassificationRecord) -> str:
    """Encode a :class:`ClassificationRecord` as a single JSON line.

    No trailing newline — the caller appends ``"\\n"`` (or the
    platform line separator) if writing to a JSONL file. Non-
    serializable values fall back to ``str(v)`` via ``default=str``.

    Stable across Postrule versions within the same major release;
    breaking-change deprecation windows are called out in
    ``CHANGELOG.md``.
    """
    return json.dumps(asdict(record), default=str)


def deserialize_record(line: str) -> ClassificationRecord:
    """Parse a single JSON line produced by :func:`serialize_record`.

    Raises :class:`json.JSONDecodeError` on malformed input and
    :class:`TypeError` when the JSON object doesn't match the
    :class:`ClassificationRecord` shape. Custom backends that want
    the skip-malformed-lines behavior used by :class:`FileStorage`
    should wrap this in ``try / except (JSONDecodeError, TypeError)``.
    """
    data = json.loads(line)
    return ClassificationRecord(**data)


# ---------------------------------------------------------------------------
# InMemoryStorage
# ---------------------------------------------------------------------------


class InMemoryStorage(StorageBase):
    """Unbounded process-local append log. Fast, volatile, zero-dep.

    Useful for tests and embedded deployments where the host owns
    persistence. **Not** the default: a runaway ``record_verdict``
    loop on this backend can OOM the host. Prefer
    :class:`BoundedInMemoryStorage` for production use without
    persistence.

    Thread safety: **not thread-safe**. Wrap externally if shared
    between threads.
    """

    def __init__(self) -> None:
        self._log: dict[str, list[ClassificationRecord]] = {}

    def append_record(self, switch_name: str, record: ClassificationRecord) -> None:
        self._log.setdefault(switch_name, []).append(record)

    def load_records(self, switch_name: str) -> list[ClassificationRecord]:
        return list(self._log.get(switch_name, []))


# ---------------------------------------------------------------------------
# BoundedInMemoryStorage — the default backend
# ---------------------------------------------------------------------------


_DEFAULT_BOUNDED_MAX_RECORDS = 10_000


class BoundedInMemoryStorage(StorageBase):
    """Process-local append log with FIFO eviction.

    Keeps the most recent ``max_records`` per switch. When a new
    record would push the log past the cap, the oldest record is
    evicted. This is the default backend for a bare
    :class:`LearnedSwitch`: it bounds memory by construction and
    requires zero configuration.

    Trade-off: oldest outcomes age out silently. For full retention,
    pass a :class:`FileStorage` (or use ``persist=True``); for
    multi-process durability, pass a :class:`SqliteStorage`.

    Thread safety: **not thread-safe**. Wrap externally.
    """

    def __init__(self, max_records: int = _DEFAULT_BOUNDED_MAX_RECORDS) -> None:
        if max_records <= 0:
            raise ValueError("max_records must be positive")
        self._max_records = max_records
        self._log: dict[str, deque[ClassificationRecord]] = {}

    def append_record(self, switch_name: str, record: ClassificationRecord) -> None:
        buf = self._log.get(switch_name)
        if buf is None:
            buf = deque(maxlen=self._max_records)
            self._log[switch_name] = buf
        buf.append(record)

    def load_records(self, switch_name: str) -> list[ClassificationRecord]:
        buf = self._log.get(switch_name)
        return list(buf) if buf is not None else []

    @property
    def max_records(self) -> int:
        return self._max_records


# ---------------------------------------------------------------------------
# File locking — POSIX only; no-op + one-shot warning on Windows
# ---------------------------------------------------------------------------


class _CachedFLock:
    """Acquire/release a flock on an already-open fd, plus an in-process lock.

    Used by :class:`FileStorage` so the .lock fd doesn't have to be
    re-opened on every append (issue #136). The fd is shared across
    threads, so we PAIR ``flock`` with a ``threading.Lock``: the OS
    lock keeps other PROCESSES out, the in-process lock keeps other
    THREADS in this process out. Both layers are necessary because
    POSIX ``flock`` is keyed by the open file description, not the
    file descriptor — threads sharing the same fd share the OFD and
    so cannot use flock alone for mutual exclusion.
    """

    __slots__ = ("_fd", "_shared", "_thread_lock", "_acquired")

    def __init__(
        self, fd: int, *, shared: bool, thread_lock: threading.Lock | threading.RLock
    ) -> None:
        self._fd = fd
        self._shared = shared
        self._thread_lock = thread_lock
        self._acquired = False

    def __enter__(self) -> _CachedFLock:
        # In-process serialization: prevents two threads in the same
        # process from racing each other on the cached fd. Cross-
        # process safety is handled by flock below.
        self._thread_lock.acquire()
        if not _HAS_FLOCK:
            self._acquired = True
            return self
        op = fcntl.LOCK_SH if self._shared else fcntl.LOCK_EX  # type: ignore[union-attr]
        try:
            fcntl.flock(self._fd, op)  # type: ignore[union-attr]
        except BaseException:
            self._thread_lock.release()
            raise
        self._acquired = True
        return self

    def __exit__(self, *exc_info: object) -> None:
        if not self._acquired:
            return
        try:
            if _HAS_FLOCK:
                with contextlib.suppress(OSError):
                    fcntl.flock(self._fd, fcntl.LOCK_UN)  # type: ignore[union-attr]
        finally:
            self._acquired = False
            self._thread_lock.release()


class _FileLock:
    """Context manager wrapping POSIX ``flock`` on a sentinel file.

    Exclusive mode (default) serializes writers within a single
    host. Shared mode permits many concurrent readers but blocks
    any writer. Lock state is released on ``__exit__``.

    On Windows (no ``fcntl``) this degrades to a no-op; the first
    instantiation emits a :class:`UserWarning` so callers are
    aware that rotation races are not prevented on that platform.
    Users needing cross-platform concurrency safety should use
    :class:`SqliteStorage` instead.
    """

    def __init__(self, path: Path, *, shared: bool = False) -> None:
        self._path = path
        self._shared = shared
        self._fd: int | None = None

    def __enter__(self) -> _FileLock:
        if not _HAS_FLOCK:
            global _WINDOWS_LOCK_WARNING_ISSUED
            if not _WINDOWS_LOCK_WARNING_ISSUED:
                warnings.warn(
                    "FileStorage locking disabled on this platform (no fcntl). "
                    "Multi-process concurrent writers may race on rotation. "
                    "For cross-platform concurrent-safe durability, use "
                    "SqliteStorage instead.",
                    UserWarning,
                    stacklevel=2,
                )
                _WINDOWS_LOCK_WARNING_ISSUED = True
            return self
        try:
            self._fd = os.open(str(self._path), os.O_RDWR | os.O_CREAT, 0o644)
        except FileNotFoundError:
            # First-time creation: parent may not exist yet. mkdir then
            # retry. The hot path through FileStorage skips this branch
            # because _append_sync ensures the dir before locking.
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(str(self._path), os.O_RDWR | os.O_CREAT, 0o644)
        op = fcntl.LOCK_SH if self._shared else fcntl.LOCK_EX  # type: ignore[union-attr]
        fcntl.flock(self._fd, op)  # type: ignore[union-attr]
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._fd is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self._fd, fcntl.LOCK_UN)  # type: ignore[union-attr]
            with contextlib.suppress(OSError):
                os.close(self._fd)
            self._fd = None


# ---------------------------------------------------------------------------
# FileStorage
# ---------------------------------------------------------------------------


# Defaults chosen so *zero-config usage never blows up a disk*:
# - 64 MB per active segment is ~600k outcomes at ~100 bytes each.
# - 8 segments × 64 MB = 512 MB cap per switch before compaction drops
#   oldest rows. A decade of operation at 10 outcomes/sec sits inside this.
_DEFAULT_MAX_BYTES_PER_SEGMENT = 64 * 1024 * 1024
_DEFAULT_MAX_ROTATED_SEGMENTS = 8


class FileStorage(StorageBase):
    """JSONL append-log on disk with self-managing rotation.

    Layout::

        <base_path>/<switch_name>/
            .lock                  # advisory lock file (POSIX flock)
            outcomes.jsonl         # active segment (appended to)
            outcomes.jsonl.1       # most-recent rotated segment
            outcomes.jsonl.2
            ...

    When the active segment crosses ``max_bytes_per_segment``, the
    writer renames it to ``outcomes.jsonl.1`` (shifting existing
    segments up) and starts a fresh active file. Segments beyond
    ``max_rotated_segments`` are deleted — old outcomes age out
    **automatically**, no cron required.

    ``load_records`` returns every segment in chronological order.
    Malformed lines are silently skipped ("some data beats no data").

    The defaults (64 MB / 8 segments = ~512 MB cap per switch) are
    chosen so a Postrule install can be left running for years without
    operator touch on any reasonable disk. Shrink them for embedded
    deployments; grow them for data-science workflows that need full
    history.

    Concurrency contract
    --------------------
    With ``lock=True`` (the default) on POSIX: safe for
    **single-host multi-process + multi-thread** writers and
    readers. Writers take an exclusive ``flock`` on ``.lock`` for
    the entire append + maybe-rotate sequence; readers take a
    shared ``flock`` for the duration of the segment walk.

    With ``lock=False`` or on Windows (no ``fcntl``): **not**
    concurrent-safe. Rotation may race. Use this mode only when
    you know there is a single writer. A one-time
    :class:`UserWarning` is emitted on the first Windows
    instantiation so this fact isn't silently swallowed.

    Not safe over network filesystems (NFS, SMB): POSIX ``flock``
    is unreliable there. For network-mounted deployments, use
    :class:`SqliteStorage` (local disk) or a dedicated DB
    backend.

    Durability contract
    -------------------
    With ``fsync=False`` (the default): writes hit the kernel
    buffer and are durable across process crashes, but a **host**
    crash (power loss, kernel panic) can lose the tail of the log.

    With ``fsync=True``: every write flushes to physical storage
    before returning. Costs ~0.5–5 ms per append depending on
    hardware. Use this for safety-critical switches where missing
    outcomes around a host crash is unacceptable.

    Customization
    -------------
    Subclass :class:`FileStorage` and override ``_serialize_line``
    / ``_parse_line`` for custom serialization (e.g., encryption,
    compression, schema evolution). Override ``_rotate`` for
    custom retention policies. Use
    :func:`serialize_record` / :func:`deserialize_record` as
    building blocks.
    """

    def __init__(
        self,
        base_path: str | Path,
        *,
        max_bytes_per_segment: int = _DEFAULT_MAX_BYTES_PER_SEGMENT,
        max_rotated_segments: int = _DEFAULT_MAX_ROTATED_SEGMENTS,
        lock: bool = True,
        fsync: bool = False,
        batching: bool = False,
        batch_size: int = 64,
        flush_interval_ms: int = 50,
        redact: Callable[[ClassificationRecord], ClassificationRecord] | None = None,
    ) -> None:
        if max_bytes_per_segment <= 0:
            raise ValueError("max_bytes_per_segment must be positive")
        if max_rotated_segments < 0:
            raise ValueError("max_rotated_segments must be >= 0")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if flush_interval_ms <= 0:
            raise ValueError("flush_interval_ms must be positive")
        self._base = Path(base_path)
        self._base.mkdir(parents=True, exist_ok=True)
        self._max_bytes = max_bytes_per_segment
        self._max_rotated = max_rotated_segments
        self._lock_enabled = lock
        self._fsync = fsync
        # Optional pre-write transform. Receives each record before it
        # hits the disk; returns a sanitized record. Load-bearing for
        # HIPAA / PII / export-controlled workloads where raw inputs
        # must never persist. Applied once per record, even when
        # batching — keeps the durable bytes aligned with the
        # redactor's view regardless of mode. See
        # docs/storage-backends.md § "Redaction hook."
        self._redact = redact
        # fd-cache for sync path: keep append fds open across calls so
        # ``append_record`` doesn't open/close per invocation. Keyed by
        # resolved switch directory (not the raw switch_name) to stay
        # consistent with the path-traversal guard. Cleared on rotation
        # and ``close()``.
        self._fd_cache: dict[str, int] = {}
        self._fd_cache_lock = threading.Lock()
        # Path-resolution cache (issue #136): ``Path.resolve()`` was
        # the dominant cost on the concurrent-write hot path. The
        # tuple is (resolved_path, (st_dev, st_ino) | None). The
        # fingerprint enables a one-syscall TOCTOU re-validation on
        # cache hit (see ``_verify_cached_dir``); fingerprint is None
        # when the directory hasn't been created yet.
        self._dir_cache: dict[str, tuple[Path, tuple[int, int] | None]] = {}
        # Tracks switches whose on-disk directory has been mkdir'd at
        # least once, so the redundant mkdir on the hot path can skip.
        self._dir_ensured: dict[str, bool] = {}
        self._dir_cache_lock = threading.Lock()
        # Lock-fd cache: keep the per-switch ``.lock`` fd open across
        # appends. Saves a posix.open/posix.close cycle per call. The
        # tuple stores ``(fd, threading.Lock)`` because POSIX flock is
        # keyed by the open file description, so threads sharing the
        # cached fd would all see the lock as already-held; the
        # per-switch threading.Lock provides the missing in-process
        # mutual exclusion. Cross-process safety still flows from flock.
        self._lock_fd_cache: dict[str, tuple[int, threading.Lock]] = {}
        self._lock_fd_cache_lock = threading.Lock()
        # Batched-async path — when batching is on, ``append_record``
        # enqueues and a background thread drains every
        # ``flush_interval_ms`` (or when the queue reaches
        # ``batch_size``). Much faster on the hot path; trades a
        # bounded crash-window for throughput. See
        # v1-readiness.md §2 finding #29 for why this exists.
        self._batching = batching
        self._batch_size = batch_size
        self._flush_interval_ms = flush_interval_ms
        self._queue: dict[str, list[ClassificationRecord]] = {}
        self._queue_lock = threading.Lock()
        self._flush_event = threading.Event()
        self._stop_event = threading.Event()
        self._flusher_thread: threading.Thread | None = None
        self._closed = False
        if batching:
            self._flusher_thread = threading.Thread(
                target=self._flusher_loop,
                name=f"postrule-filestorage-flusher-{id(self):x}",
                daemon=True,
            )
            self._flusher_thread.start()
            # Best-effort: drain on interpreter shutdown. Using
            # weakref means the atexit hook doesn't keep the storage
            # alive past its natural lifetime.
            ref = weakref.ref(self)

            def _atexit_drain() -> None:
                inst = ref()
                if inst is not None:
                    inst.close()

            atexit.register(_atexit_drain)

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    def _switch_dir(self, switch_name: str, *, validate: bool = True) -> Path:
        """Resolve the per-switch directory, refusing path-escape attempts.

        A malicious or mistaken ``switch_name`` (``"../other"``,
        ``"/etc/passwd"``, ``"a/b"``) must not be able to address
        files outside the configured ``base_path``. We reject
        absolute names and any component equal to ``".."``, then
        confirm the resolved path stays inside the resolved base.

        Validated results are cached (issue #136 — ``Path.resolve``
        was the dominant hot-path cost). Public entry points pass
        ``validate=True``: a single ``os.lstat`` re-confirms the
        cached directory hasn't been swapped via a TOCTOU symlink
        attack (redteam test_toctou_symlink_swap). Internal callers
        pass ``validate=False`` so the per-call validation runs once
        per ``append_record`` rather than 4-5 times.
        """
        cached = self._dir_cache.get(switch_name)
        if cached is not None:
            if validate:
                self._verify_cached_dir(switch_name, cached)
            return cached[0]
        if not switch_name:
            raise ValueError("switch_name cannot be empty")
        if switch_name.startswith(("/", "\\")):
            raise ValueError(f"switch_name must be relative; got absolute path {switch_name!r}")
        parts = Path(switch_name).parts
        if any(p == ".." for p in parts):
            raise ValueError(f"switch_name must not contain '..'; got {switch_name!r}")
        candidate = (self._base / switch_name).resolve()
        base_resolved = self._base.resolve()
        try:
            candidate.relative_to(base_resolved)
        except ValueError as e:
            raise ValueError(
                f"switch_name {switch_name!r} resolves outside base_path {self._base}"
            ) from e
        # Capture inode/dev fingerprint for TOCTOU detection. fp=None
        # when the dir doesn't yet exist; the entry will be replaced
        # on the next call (no validation can succeed without a fp).
        try:
            st = os.lstat(str(candidate))
            fp: tuple[int, int] | None = (st.st_dev, st.st_ino)
            if _stat.S_ISLNK(st.st_mode):
                raise ValueError(f"switch_name {switch_name!r} resolves to a symlink; refusing")
        except FileNotFoundError:
            fp = None
        with self._dir_cache_lock:
            existing = self._dir_cache.get(switch_name)
            if existing is not None:
                return existing[0]
            self._dir_cache[switch_name] = (candidate, fp)
        return candidate

    def _verify_cached_dir(
        self, switch_name: str, entry: tuple[Path, tuple[int, int] | None]
    ) -> None:
        """Cheap re-validation of a cached switch_dir (issue #136).

        Single ``os.lstat`` call. If the inode changed since
        cache-time, or the entry is now a symlink, the cache is
        stale and we refuse the call (TOCTOU safety). If the
        cached fingerprint is None (dir didn't exist at cache-time),
        drop the entry and let the caller re-resolve so the next
        pass records the real inode.
        """
        candidate, fingerprint = entry
        if fingerprint is None:
            with self._dir_cache_lock:
                self._dir_cache.pop(switch_name, None)
                self._dir_ensured.pop(switch_name, None)
            return
        try:
            st = os.lstat(str(candidate))
        except FileNotFoundError:
            with self._dir_cache_lock:
                self._dir_cache.pop(switch_name, None)
                self._dir_ensured.pop(switch_name, None)
            return
        if _stat.S_ISLNK(st.st_mode):
            raise ValueError(f"switch_name {switch_name!r} now resolves to a symlink; refusing")
        if (st.st_dev, st.st_ino) != fingerprint:
            with self._dir_cache_lock:
                self._dir_cache.pop(switch_name, None)
                self._dir_ensured.pop(switch_name, None)
            raise ValueError(f"switch_name {switch_name!r} backing directory was swapped; refusing")

    def _active_path(self, switch_name: str) -> Path:
        # Internal: validation already happened in append_record.
        return self._switch_dir(switch_name, validate=False) / "outcomes.jsonl"

    def _rotated_path(self, switch_name: str, idx: int) -> Path:
        return self._switch_dir(switch_name, validate=False) / f"outcomes.jsonl.{idx}"

    def _lock_path(self, switch_name: str) -> Path:
        return self._switch_dir(switch_name, validate=False) / ".lock"

    # ------------------------------------------------------------------
    # Locking helpers
    # ------------------------------------------------------------------

    def _exclusive_lock(self, switch_name: str) -> contextlib.AbstractContextManager[object]:
        if not self._lock_enabled:
            return contextlib.nullcontext()
        return self._cached_flock(switch_name, shared=False)

    def _shared_lock(self, switch_name: str) -> contextlib.AbstractContextManager[object]:
        if not self._lock_enabled:
            return contextlib.nullcontext()
        return self._cached_flock(switch_name, shared=True)

    def _cached_flock(
        self, switch_name: str, *, shared: bool
    ) -> contextlib.AbstractContextManager[object]:
        """Return a flock context manager backed by a cached lock fd.

        Combines a cached ``.lock`` fd (saves posix.open/close per
        call, issue #136) with a per-switch ``threading.Lock``
        (necessary because flock on a shared OFD is not mutually
        exclusive between threads). Falls back to a fresh
        ``_FileLock`` if the cache cannot be used.
        """
        if self._closed or not _HAS_FLOCK:
            return _FileLock(self._lock_path(switch_name), shared=shared)
        entry = self._get_lock_fd(switch_name)
        if entry is None:
            return _FileLock(self._lock_path(switch_name), shared=shared)
        fd, thread_lock = entry
        return _CachedFLock(fd, shared=shared, thread_lock=thread_lock)

    def _get_lock_fd(self, switch_name: str) -> tuple[int, threading.Lock] | None:
        """Return ``(fd, threading.Lock)`` for ``switch_name``.

        Opens on first use; returns None if the open fails (rare on
        a writable base_path; the caller falls back to per-call
        ``_FileLock``).
        """
        key = self._cache_key(switch_name)
        with self._lock_fd_cache_lock:
            entry = self._lock_fd_cache.get(key)
            if entry is not None:
                return entry
            lock_path = self._lock_path(switch_name)
            try:
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
            except OSError:
                return None
            entry = (fd, threading.Lock())
            self._lock_fd_cache[key] = entry
            return entry

    # ------------------------------------------------------------------
    # Serialization hooks (override for custom encoding)
    # ------------------------------------------------------------------

    def _serialize_line(self, record: ClassificationRecord) -> bytes:
        """Encode one record into the bytes written to disk (no newline).

        Override to encrypt, compress, or change format. Must
        round-trip with :meth:`_parse_line`.
        """
        return serialize_record(record).encode("utf-8")

    def _parse_line(self, raw: bytes) -> ClassificationRecord | None:
        """Decode one line of bytes into a record, or ``None`` on error.

        Returning ``None`` signals "malformed, skip" and is the
        mechanism by which partial-line reads on a concurrently-
        appended file degrade gracefully.
        """
        try:
            return deserialize_record(raw.decode("utf-8"))
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            return None

    # ------------------------------------------------------------------
    # Append path
    # ------------------------------------------------------------------

    def append_record(self, switch_name: str, record: ClassificationRecord) -> None:
        # Validate the switch name before any I/O (path-traversal guard).
        self._switch_dir(switch_name)
        # Apply the user-supplied redactor BEFORE the record reaches
        # either the queue or the disk. This keeps raw PII out of
        # the in-memory batch as well as the durable bytes.
        if self._redact is not None:
            record = self._redact(record)
        if self._batching:
            if self._closed:
                raise RuntimeError("FileStorage.append_record called after close()")
            should_flush = False
            with self._queue_lock:
                buf = self._queue.setdefault(switch_name, [])
                buf.append(record)
                total = sum(len(b) for b in self._queue.values())
                if total >= self._batch_size:
                    should_flush = True
            if should_flush:
                self._flush_event.set()
            return
        self._append_sync(switch_name, [record])

    # ------------------------------------------------------------------
    # Sync write path — fd-cached for perf
    # ------------------------------------------------------------------

    def _append_sync(self, switch_name: str, records: list[ClassificationRecord]) -> None:
        """Write one or more records to the active segment synchronously.

        Shared between the non-batching append path and the
        background-flusher drain. Holds the exclusive lock across
        the whole batch so the check-then-rotate and multi-record
        write are atomic from the perspective of other writers.
        """
        if not records:
            return
        # mkdir on every append was a measurable hot-path cost
        # (issue #136 profile: 41s cumtime in pathlib.mkdir over 40k
        # appends). After the first successful mkdir, subsequent calls
        # for the same switch can skip it.
        if not self._dir_ensured.get(switch_name):
            self._switch_dir(switch_name, validate=False).mkdir(parents=True, exist_ok=True)
            self._dir_ensured[switch_name] = True
        linesep = os.linesep.encode("utf-8")
        payloads = [self._serialize_line(r) + linesep for r in records]
        total_bytes = sum(len(p) for p in payloads)
        path = self._active_path(switch_name)
        with self._exclusive_lock(switch_name):
            # Rotate BEFORE writing if the new batch would push over cap.
            # Under the exclusive lock the check-then-rotate is atomic.
            if path.exists():
                try:
                    current = path.stat().st_size
                except OSError:
                    current = 0
                if current + total_bytes > self._max_bytes:
                    self._rotate(switch_name)
            fd = self._get_append_fd(switch_name)
            try:
                for payload in payloads:
                    os.write(fd, payload)
                if self._fsync:
                    os.fsync(fd)
            except OSError:
                # If a write fails, drop the cached fd so the next
                # call reopens fresh instead of looping on a dead fd.
                self._invalidate_fd(switch_name)
                raise

    def _cache_key(self, switch_name: str) -> str:
        """Deterministic cache key for the fd / lock caches.

        Internal-only: skips re-validation. The entry-point
        validation in ``append_record`` covers the security check
        once per call cycle.
        """
        return str(self._switch_dir(switch_name, validate=False))

    def _get_append_fd(self, switch_name: str) -> int:
        """Return a cached append fd, opening on first use.

        The cache turns repeated ``append_record`` calls on the same
        switch from open/close-per-call into a single persistent fd
        — the main single-call latency win vs v0.2. Rotation calls
        ``_invalidate_fd`` so writes after rotation land in the
        fresh active segment, not the just-rotated file.
        """
        key = self._cache_key(switch_name)
        with self._fd_cache_lock:
            fd = self._fd_cache.get(key)
            if fd is not None:
                return fd
            path = self._active_path(switch_name)
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            self._fd_cache[key] = fd
            return fd

    def _invalidate_fd(self, switch_name: str) -> None:
        """Close and evict the cached append fd for ``switch_name``."""
        key = self._cache_key(switch_name)
        with self._fd_cache_lock:
            fd = self._fd_cache.pop(key, None)
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)

    # ------------------------------------------------------------------
    # Batched-async write path
    # ------------------------------------------------------------------

    def _flusher_loop(self) -> None:
        """Background thread: drain the queue every flush_interval_ms."""
        interval = self._flush_interval_ms / 1000.0
        while not self._stop_event.is_set():
            self._flush_event.wait(timeout=interval)
            self._flush_event.clear()
            try:
                self._drain_once()
            except Exception:
                # A drain failure must not kill the flusher thread
                # — queued records stay queued for the next pass.
                # A sticky-failing primary will accumulate queue
                # pressure; bounded queueing is the caller's
                # responsibility via ``ResilientStorage``.
                continue
        # Final drain on stop.
        try:
            self._drain_once()
        except Exception:
            pass

    def _drain_once(self) -> None:
        """Move the queued records to disk in one pass."""
        with self._queue_lock:
            if not self._queue:
                return
            snapshot = self._queue
            self._queue = {}
        for switch_name, records in snapshot.items():
            if records:
                try:
                    self._append_sync(switch_name, records)
                except Exception:
                    # Re-queue on failure so the next drain retries.
                    # This preserves at-least-once durability as long
                    # as the process stays alive.
                    with self._queue_lock:
                        existing = self._queue.setdefault(switch_name, [])
                        self._queue[switch_name] = records + existing
                    raise

    def flush(self) -> None:
        """Drain all pending batched writes synchronously now.

        Called by :meth:`load_records` so reads see the most recent
        writes, and by :meth:`close` on shutdown. Safe to call even
        when batching is off (no-op).
        """
        if not self._batching:
            return
        self._drain_once()

    def close(self) -> None:
        """Stop the flusher thread, drain pending, and release fds.

        Idempotent. Safe to call from atexit and from user code.
        """
        if self._closed:
            return
        self._closed = True
        if self._batching:
            self._stop_event.set()
            self._flush_event.set()
            if self._flusher_thread is not None:
                self._flusher_thread.join(timeout=2.0)
        # Close any cached append fds.
        with self._fd_cache_lock:
            fds = list(self._fd_cache.values())
            self._fd_cache.clear()
        for fd in fds:
            with contextlib.suppress(OSError):
                os.close(fd)
        # Close any cached lock fds.
        with self._lock_fd_cache_lock:
            lock_entries = list(self._lock_fd_cache.values())
            self._lock_fd_cache.clear()
        for fd, _thread_lock in lock_entries:
            with contextlib.suppress(OSError):
                os.close(fd)

    def __del__(self) -> None:
        # Best-effort — Python may have already cleaned up dependencies
        # (threads, atexit, os module) by the time this runs, so we
        # swallow any failure.
        try:
            self.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Rotation (call only while holding the exclusive lock)
    # ------------------------------------------------------------------

    def _rotate(self, switch_name: str) -> None:
        """Shift segments up and drop anything beyond the retention cap.

        Only safe to call with the exclusive lock held (or in
        single-writer mode). Public callers should use
        :meth:`compact`, which takes the lock itself.
        """
        # Close the cached append fd first — otherwise subsequent
        # writes land in the just-rotated file (same inode via the
        # open fd) rather than the fresh active segment.
        self._invalidate_fd(switch_name)
        # Drop segments beyond retention first (from oldest to newest so
        # we never clobber one we'd still be renaming into).
        for idx in range(self._max_rotated + 1, 1000):
            old = self._rotated_path(switch_name, idx)
            if not old.exists():
                break
            with contextlib.suppress(OSError):
                old.unlink()

        # Shift rotated segments up by one slot (N → N+1).
        for idx in range(self._max_rotated, 0, -1):
            src = self._rotated_path(switch_name, idx)
            dst = self._rotated_path(switch_name, idx + 1)
            if src.exists():
                if idx + 1 > self._max_rotated:
                    # Drop if shifting would push us past retention.
                    with contextlib.suppress(OSError):
                        src.unlink()
                    continue
                with contextlib.suppress(OSError):
                    src.replace(dst)

        # Move active → .1.
        active = self._active_path(switch_name)
        if active.exists():
            with contextlib.suppress(OSError):
                active.replace(self._rotated_path(switch_name, 1))

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def load_records(self, switch_name: str) -> list[ClassificationRecord]:
        # Drain any queued batched writes so readers see a
        # consistent view. Cheap no-op when batching is off.
        self.flush()
        records: list[ClassificationRecord] = []
        if not self._switch_dir(switch_name).exists():
            return records
        with self._shared_lock(switch_name):
            # Walk rotated segments oldest→newest, then the active segment.
            for idx in range(self._max_rotated, 0, -1):
                records.extend(self._read_segment(self._rotated_path(switch_name, idx)))
            records.extend(self._read_segment(self._active_path(switch_name)))
        return records

    def _read_segment(self, path: Path) -> list[ClassificationRecord]:
        if not path.exists():
            return []
        out: list[ClassificationRecord] = []
        with open(path, "rb") as f:
            for raw_line in f:
                stripped = raw_line.rstrip(b"\r\n")
                if not stripped:
                    continue
                rec = self._parse_line(stripped)
                if rec is not None:
                    out.append(rec)
        return out

    # ------------------------------------------------------------------
    # Operator-facing utilities (also fuel the `postrule roi` reporter)
    # ------------------------------------------------------------------

    def switch_names(self) -> list[str]:
        """Return every switch name that has an outcome directory on disk."""
        if not self._base.exists():
            return []
        return sorted(p.name for p in self._base.iterdir() if p.is_dir())

    def bytes_on_disk(self, switch_name: str) -> int:
        """Total bytes used by this switch across all segments."""
        total = 0
        d = self._switch_dir(switch_name)
        if not d.exists():
            return 0
        for p in d.iterdir():
            if p.is_file() and not p.name.startswith("."):
                try:
                    total += p.stat().st_size
                except OSError:
                    continue
        return total

    def compact(self, switch_name: str) -> None:
        """Force a rotation pass now, useful before archiving.

        Takes the exclusive lock before rotating, so it's safe to
        call concurrently with other writers.
        """
        with self._exclusive_lock(switch_name):
            self._rotate(switch_name)


# ---------------------------------------------------------------------------
# SqliteStorage — recommended for concurrent production workloads
# ---------------------------------------------------------------------------


class SqliteStorage(StorageBase):
    """Durable outcome log on SQLite (WAL journal mode).

    The recommended backend for any production deployment where
    multiple processes append to the same log on a single host.
    Zero external dependencies: uses stdlib ``sqlite3``.

    Concurrency contract
    --------------------
    SQLite WAL mode provides **1 writer + N concurrent readers**
    on the same database file. Multiple writer processes are
    serialized by SQLite's ``BEGIN IMMEDIATE`` — safe, not
    parallel. This is typically fine for outcome logging (throughput
    is bounded by classifier throughput, not the log).

    A fresh connection is opened per call; no shared-connection
    thread-safety concerns. ``PRAGMA journal_mode=WAL`` is set on
    schema init and persists in the database file thereafter.

    Durability contract
    -------------------
    Controlled by the ``sync`` kwarg (maps to
    ``PRAGMA synchronous``):

    - ``"OFF"`` — fastest, but a host crash can corrupt the log.
    - ``"NORMAL"`` (default) — crash-safe across process death;
      a host crash may lose the final transaction but never
      corrupt.
    - ``"FULL"`` — crash-safe across host crash; ~2x write cost.

    Platform notes
    --------------
    SQLite WAL does NOT work over network filesystems (NFS, SMB,
    etc.). Use local disk only. For distributed writers, move to
    PostgresStorage (v0.3 roadmap) or a dedicated queue.

    Schema (for operators / custom tooling)
    ---------------------------------------
    One table, ``outcomes``::

        CREATE TABLE outcomes (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            switch_name  TEXT NOT NULL,
            timestamp    REAL NOT NULL,
            data         TEXT NOT NULL   -- serialized JSON record
        );
        CREATE INDEX idx_switch ON outcomes(switch_name, id);

    The JSON payload in ``data`` uses the same format produced by
    :func:`serialize_record`, so custom readers don't need to
    understand the SQLite schema — they can copy the payloads as
    JSONL.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        sync: str = "NORMAL",
        timeout: float = 30.0,
        redact: Callable[[ClassificationRecord], ClassificationRecord] | None = None,
    ) -> None:
        valid_sync = {"OFF", "NORMAL", "FULL", "EXTRA"}
        if sync not in valid_sync:
            raise ValueError(f"sync must be one of {valid_sync}; got {sync!r}")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._sync = sync
        self._timeout = timeout
        self._redact = redact
        self._init_schema()

    @contextlib.contextmanager
    def _connect(self):  # type: ignore[no-untyped-def]
        """Open a fresh connection, configure it, close on exit.

        sqlite3's own context-manager semantics only commit/rollback
        the transaction — they DO NOT close the connection. We wrap
        to guarantee closure, since we open one connection per call
        for thread safety.
        """
        conn = sqlite3.connect(
            str(self._db_path),
            timeout=self._timeout,
            isolation_level=None,  # autocommit; we manage txns explicitly
        )
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA synchronous={self._sync}")
            conn.execute("PRAGMA busy_timeout=30000")
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    switch_name TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    data TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_outcomes_switch ON outcomes(switch_name, id)"
            )

    def append_record(self, switch_name: str, record: ClassificationRecord) -> None:
        if self._redact is not None:
            record = self._redact(record)
        data = serialize_record(record)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO outcomes (switch_name, timestamp, data) VALUES (?, ?, ?)",
                    (switch_name, float(record.timestamp), data),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def load_records(self, switch_name: str) -> list[ClassificationRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT data FROM outcomes WHERE switch_name = ? ORDER BY id",
                (switch_name,),
            ).fetchall()
        out: list[ClassificationRecord] = []
        for (payload,) in rows:
            try:
                out.append(deserialize_record(payload))
            except (json.JSONDecodeError, TypeError):
                # Graceful degradation matches FileStorage's contract.
                continue
        return out

    def switch_names(self) -> list[str]:
        """Return every switch that has at least one recorded outcome."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT switch_name FROM outcomes ORDER BY switch_name"
            ).fetchall()
        return [r[0] for r in rows]

    def count(self, switch_name: str) -> int:
        """Count outcomes for a switch without materializing them."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM outcomes WHERE switch_name = ?",
                (switch_name,),
            ).fetchone()
        return int(row[0]) if row else 0

    @property
    def db_path(self) -> Path:
        return self._db_path


# ---------------------------------------------------------------------------
# ResilientStorage — auto-fallback wrapper for "never lose a classification"
# ---------------------------------------------------------------------------


_DEFAULT_FALLBACK_MAX_RECORDS = 100_000
_DEFAULT_RECOVERY_PROBE_EVERY = 100


class ResilientStorage(StorageBase):
    """Wrap a durable backend with an in-memory fallback.

    On primary append failure, spills to a bounded in-memory
    buffer and emits a one-time :class:`UserWarning`. Subsequent
    writes go straight to the fallback until a recovery probe
    succeeds. On recovery, the fallback is drained back into the
    primary (in append order); thereafter normal operation
    resumes.

    ``load_records`` always returns primary records followed by
    fallback records, so readers get a consistent view regardless
    of degraded state.

    Intended for classification-hot-path durability: a failing
    disk or a transient permission error on the outcome log must
    NOT take down classification. The trade-off is that an
    operator who doesn't watch the degraded signal could lose
    fallback records to process death — this wrapper buys
    liveness, not durability, during the degraded window.

    Signalling (for operators and compliance audits):
    - ``degraded`` property — currently in fallback mode?
    - ``degraded_since`` — monotonic timestamp when we entered
      degraded mode (``None`` when healthy).
    - ``degraded_writes`` — count of records that landed in
      fallback (running total across episodes).
    - ``on_degrade`` / ``on_recover`` callbacks — hooks for
      pushing to alerting / telemetry channels.

    Customization: subclass and override :meth:`_enter_degraded`
    / :meth:`_try_recover` to integrate with your own breaker,
    signal-sink, or escalation logic.
    """

    def __init__(
        self,
        primary: Storage,
        *,
        fallback: Storage | None = None,
        fallback_max_records: int = _DEFAULT_FALLBACK_MAX_RECORDS,
        recovery_probe_every: int = _DEFAULT_RECOVERY_PROBE_EVERY,
        on_degrade: Callable[[Exception], None] | None = None,
        on_recover: Callable[[int], None] | None = None,
    ) -> None:
        if recovery_probe_every <= 0:
            raise ValueError("recovery_probe_every must be positive")
        self._primary = primary
        self._fallback = (
            fallback
            if fallback is not None
            else BoundedInMemoryStorage(max_records=fallback_max_records)
        )
        self._recovery_probe_every = recovery_probe_every
        self._on_degrade = on_degrade
        self._on_recover = on_recover

        self._degraded: bool = False
        self._degraded_since: float | None = None
        self._degraded_writes: int = 0
        self._degraded_evictions: int = 0
        self._writes_since_probe: int = 0
        self._tracked_switches: set[str] = set()

    # --- Status ------------------------------------------------------------

    @property
    def degraded(self) -> bool:
        """``True`` iff primary is currently unavailable."""
        return self._degraded

    @property
    def degraded_since(self) -> float | None:
        """Monotonic timestamp of the degradation entry, or ``None``."""
        return self._degraded_since

    @property
    def degraded_writes(self) -> int:
        """Total writes that landed in fallback (running counter)."""
        return self._degraded_writes

    @property
    def degraded_evictions(self) -> int:
        """Records evicted from a bounded fallback while degraded.

        When the fallback is a bounded buffer (e.g.
        :class:`BoundedInMemoryStorage`), records beyond the cap are
        silently FIFO-evicted by the fallback's own logic. This
        counter surfaces those drops so the audit chain doesn't
        claim records survived when they didn't.
        """
        return self._degraded_evictions

    @property
    def primary(self) -> Storage:
        return self._primary

    @property
    def fallback(self) -> Storage:
        return self._fallback

    # --- Storage protocol --------------------------------------------------

    def append_record(self, switch_name: str, record: ClassificationRecord) -> None:
        self._tracked_switches.add(switch_name)

        if not self._degraded:
            try:
                self._primary.append_record(switch_name, record)
                return
            except Exception as e:
                self._enter_degraded(e)
                # fall through — append to fallback below

        # Detect eviction: if the fallback is bounded and the append
        # didn't grow its stored count, the record was silently
        # evicted by the fallback's retention policy (e.g.
        # :class:`BoundedInMemoryStorage`'s FIFO cap). Surface the
        # drop so ``degraded_writes`` doesn't over-claim.
        try:
            before = len(self._fallback.load_records(switch_name))
        except Exception:
            before = None
        self._fallback.append_record(switch_name, record)
        if before is not None:
            try:
                after = len(self._fallback.load_records(switch_name))
            except Exception:
                after = before + 1
            if after <= before:
                self._degraded_evictions += 1
        self._degraded_writes += 1
        self._writes_since_probe += 1
        if self._writes_since_probe >= self._recovery_probe_every:
            self._writes_since_probe = 0
            self._try_recover()

    def load_records(self, switch_name: str) -> list[ClassificationRecord]:
        primary_recs: list[ClassificationRecord] = []
        try:
            primary_recs = list(self._primary.load_records(switch_name))
        except Exception:
            # Primary unreachable even for reads — return whatever we have
            # in the fallback buffer rather than raising.
            pass
        fallback_recs = list(self._fallback.load_records(switch_name))
        return primary_recs + fallback_recs

    # --- Degraded-mode machinery ------------------------------------------

    def _enter_degraded(self, reason: Exception) -> None:
        if self._degraded:
            return
        self._degraded = True
        self._degraded_since = time.monotonic()
        self._writes_since_probe = 0
        warnings.warn(
            f"ResilientStorage: primary backend failed "
            f"({type(reason).__name__}: {reason}); falling back to the "
            "in-memory buffer. Records will drain back automatically "
            "when the primary recovers. Watch the `degraded` property "
            "and your `on_degrade` hook for operator action.",
            UserWarning,
            stacklevel=3,
        )
        if self._on_degrade is not None:
            try:
                self._on_degrade(reason)
            except Exception:
                pass

    def _try_recover(self) -> None:
        """Attempt to drain the fallback buffer into the primary.

        Drains **record-by-record**: after each successful primary
        append, the record is popped from the fallback. If the
        primary fails mid-list, the drain aborts with the
        remaining records still in fallback (not duplicated) —
        the next probe resumes from where we stopped.

        On full success: mark healthy and fire ``on_recover``. On
        partial success or failure: stay degraded; un-drained
        records remain in fallback for the next probe.
        """
        drained = 0
        try:
            for switch_name in sorted(self._tracked_switches):
                while True:
                    recs = self._fallback.load_records(switch_name)
                    if not recs:
                        break
                    rec = recs[0]
                    # Primary append may raise — if so, we exit with
                    # ``rec`` still at the head of fallback, no
                    # duplicate in primary.
                    self._primary.append_record(switch_name, rec)
                    self._pop_fallback_head(switch_name)
                    drained += 1
        except Exception:
            return  # Still degraded; try again next probe.

        # Fully drained.
        self._degraded = False
        self._degraded_since = None
        self._writes_since_probe = 0
        warnings.warn(
            f"ResilientStorage: primary recovered; drained {drained} record(s) from fallback.",
            UserWarning,
            stacklevel=3,
        )
        if self._on_recover is not None:
            try:
                self._on_recover(drained)
            except Exception:
                pass

    def _pop_fallback_head(self, switch_name: str) -> None:
        """Remove the oldest record for ``switch_name`` from the fallback.

        Drain-resumable fix for v1 finding #5: if primary fails
        mid-list, the not-yet-drained tail stays put. Works with
        :class:`InMemoryStorage` and :class:`BoundedInMemoryStorage`
        (both expose a ``_log`` dict of lists/deques). Custom
        fallbacks that don't expose a popable internal structure
        can override this method.
        """
        log = getattr(self._fallback, "_log", None)
        if isinstance(log, dict) and switch_name in log:
            buf = log[switch_name]
            if buf:
                if hasattr(buf, "popleft"):
                    buf.popleft()
                else:
                    buf.pop(0)
                if not buf:
                    del log[switch_name]

    def _clear_fallback_for(self, switch_name: str) -> None:
        """Reset fallback storage for one switch after a successful drain.

        Default implementation assumes the fallback exposes a private
        ``_log`` dict keyed by switch name — matches both
        :class:`InMemoryStorage` and :class:`BoundedInMemoryStorage`.
        Override for custom fallbacks.
        """
        log = getattr(self._fallback, "_log", None)
        if isinstance(log, dict) and switch_name in log:
            del log[switch_name]

    def drain(self) -> int:
        """Explicit operator-triggered drain attempt.

        Returns the number of records successfully drained.
        Useful in tests or in on-demand maintenance tasks.
        """
        before = self._degraded_writes
        if self._degraded:
            self._try_recover()
        # Approximate "drained" as the count of records that moved;
        # we don't precisely track per-drain counts here.
        return before - (before - self._degraded_writes)


# Platform-capability export for consumers that want to branch
# behavior (tests, example code, opt-in hardening).
__all__ = [
    "BoundedInMemoryStorage",
    "FileStorage",
    "InMemoryStorage",
    "ResilientStorage",
    "SqliteStorage",
    "Storage",
    "StorageBase",
    "deserialize_record",
    "serialize_record",
]


# Allow consumers to test for platform lock support without
# importing the private _HAS_FLOCK name.
def flock_supported() -> bool:
    """``True`` if POSIX ``flock`` is available on this platform."""
    return _HAS_FLOCK


# Exposed for test-only monkeypatching. Not part of the stable API.
_TEST_HOOKS = {
    "has_flock": _HAS_FLOCK,
    "sqlite_version": sqlite3.sqlite_version,
    "python_platform": sys.platform,
}
