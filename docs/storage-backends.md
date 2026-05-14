# Storage backends — guarantees, limitations, customization

**Status:** v0.2.x. Owner: Benjamin Booth.
Relevant code: `src/postrule/storage.py`, tests under
`tests/test_storage*.py`.

Every downstream decision Postrule makes — phase graduation,
drift detection, ROI estimation, multi-language model comparison — reads
off the outcome log, so the storage backend choice is the
deployment knob with the largest blast radius.

## Backend matrix

| Backend | Durability | Concurrency | Bounded | Deps | Good for |
|---|---|---|---|---|---|
| [`BoundedInMemoryStorage`](#boundedinmemorystorage) | None (process-local) | Single thread | Yes (FIFO cap) | stdlib | Default. Dev, tests, short-lived processes where you don't need history beyond `max_records`. |
| [`InMemoryStorage`](#inmemorystorage) | None | Single thread | **No — unbounded** | stdlib | Tests, embedded use where the host owns persistence. Can OOM. |
| [`FileStorage`](#filestorage) | Durable across process restart; host-crash durability is opt-in via `fsync=True` | **Single-host** multi-process + multi-thread with POSIX `flock` | Yes (segment rotation) | stdlib | Single-host deployments; dev and staging; embedded production where the process owns the disk. |
| [`SqliteStorage`](#sqlitestorage) | ACID within the DB; durability via `PRAGMA synchronous` | 1 writer + N readers within a host (WAL mode) | No (DB grows) | stdlib | **Recommended for multi-process production** on a single host. |
| [`ResilientStorage`](#resilientstorage) | Inherits primary | Inherits primary | Fallback buffer bounded | stdlib | **Wrapper**, not standalone. Adds auto-fallback + drain-on-recovery around any primary. Used by `persist=True` by default. |
| *Future: `PostgresStorage`* | ACID; distributed | Full DB concurrency | No | optional extra | Multi-host / distributed production. v0.3+ roadmap. |

The default when you construct a bare `LearnedSwitch` is
`BoundedInMemoryStorage(max_records=10_000)` — safe for any
process but volatile. Pass `persist=True` to get `FileStorage`
rooted at `./runtime/postrule/` (single-host durable), or pass
`storage=SqliteStorage("./runtime/postrule.db")` for the concurrent-
safe option.

## BoundedInMemoryStorage

### Guarantees
- Keeps the most recent `max_records` per switch (FIFO eviction).
- Bounded memory by construction — a runaway `record_verdict`
  loop cannot OOM the host.
- Records round-trip with perfect fidelity while resident.

### Limitations
- **Not durable.** Process death = log gone.
- **Not thread-safe.** If you share a switch across threads, wrap
  externally or use `SqliteStorage`.
- Oldest records age out silently when the cap is hit; no
  callback, no warning.

### ⚠️ Low-verdict-rate footgun

`BoundedInMemoryStorage` evicts FIFO regardless of verdict
status. With `auto_record=True` (the default), every classify
appends an `UNKNOWN` row, and only a fraction of those get
upgraded to `CORRECT`/`INCORRECT` later via
`record_verdict` / a `verifier=`.

**If your verdict rate is low (under ~2%), the cap rolls past
your verdict-bearing rows before they accumulate, and the gate
starves silently.** It just sees `UNKNOWN`, never advances, and
never errors — the classifier looks fine but never graduates.

Two fixes:

- **Best:** `persist=True` (or `storage=SqliteStorage(...)`).
  Durable backends keep history and rotate large logs without
  losing verdicts.
- **OK if you really want in-mem:** raise `max_records` so the
  retention window comfortably covers `gate.min_paired / verdict_rate`.
  At a 1% verdict rate and `min_paired=200`, that's ~20,000 records,
  not the default 10,000.

The same warning applies in [`getting-started.md`](getting-started.md);
if you skip durable storage in production, set the cap deliberately.

### When to use
- Dev / tests.
- Short-lived CLI tools that classify and exit.
- Deployments where an external system (queue, APM, data
  pipeline) owns durable persistence and Postrule is the in-
  process routing layer only.

## InMemoryStorage

### Guarantees
- All records retained verbatim for the life of the process.

### Limitations
- **Unbounded.** Memory grows without limit. An overnight
  classify-and-record loop will OOM the host.
- Not the default — you have to pass it explicitly.
- Not thread-safe.

### When to use
- Tests that assert on exact record counts.
- Tightly-controlled workloads where you know record volume and
  want zero eviction surprise.

## FileStorage

### Guarantees
- **Durability across process restart.** Writes hit the kernel
  buffer. With `fsync=True`, writes hit physical storage before
  returning.
- **Single-host concurrency** when `lock=True` (default) on
  POSIX: multiple processes and threads can write concurrently
  and will serialize at the `flock` sentinel. Readers take a
  shared lock and block only during rotation.
- **Bounded disk use:** segments cap at `max_bytes_per_segment`
  (default 64 MB); retention caps at `max_rotated_segments`
  (default 8). Old outcomes age out automatically — no cron.
- **Malformed-line tolerance:** partial / corrupt JSON lines are
  silently skipped by the reader (`some data beats no data`).

### Limitations
- **POSIX `flock` only.** Windows has no equivalent; Postrule
  falls back to a no-op lock and emits a one-time
  `UserWarning`. For cross-platform concurrency use
  `SqliteStorage`.
- **Not safe over network filesystems** (NFS, SMB, CIFS).
  Advisory file locking is unreliable across NFS. Use a local
  disk, or switch to a DB backend.
- **Host-crash durability requires `fsync=True`.** Without it,
  a power loss or kernel panic can lose the tail of the log
  (typically the last few seconds of writes).
- **Retention is count-based, not time-based.** If your traffic
  is bursty, old segments age out by byte count regardless of
  wall-clock age.

### Configuration knobs
```python
from postrule import FileStorage

storage = FileStorage(
    "./runtime/postrule",
    max_bytes_per_segment=64 * 1024 * 1024,   # 64 MB
    max_rotated_segments=8,                    # 8 × 64 MB retained
    lock=True,                                 # POSIX flock on append + rotate
    fsync=False,                               # True for host-crash durability
    batching=False,                            # see "Sync vs batched" below
    batch_size=64,                             # records per flush when batching=True
    flush_interval_ms=50,                      # max wait before a flush
)
```

### Redaction hook

For HIPAA, PII, and export-control workloads where raw inputs
must never reach the durable outcome log, both `FileStorage` and
`SqliteStorage` accept a `redact` kwarg:

```python
from dataclasses import replace
from postrule import ClassificationRecord, FileStorage

def scrub_pii(record: ClassificationRecord) -> ClassificationRecord:
    return replace(record, input="<redacted>")

storage = FileStorage("./runtime/postrule", redact=scrub_pii)
```

The redactor runs once per `append_record`, before the record
is queued (when `batching=True`) or written to disk — so raw
PII never lands in the in-memory batch either. Return a new
`ClassificationRecord` (use `dataclasses.replace` or construct a
new instance) with the sensitive fields sanitized; other fields
flow through unchanged. Shadow observations (`rule_output`,
`model_output`, `ml_output`) are separate fields; redact each as
your policy requires.

The hook is an intentional seam — keep the redactor fast
(< 100 µs) since it runs on the hot path. For heavyweight
redaction (regex, external de-identification services), wrap
with a `ResilientStorage` so a slow redactor can't take down
classification:

```python
from postrule import FileStorage, ResilientStorage

storage = ResilientStorage(FileStorage("./runtime/postrule", redact=scrub_pii))
```

### Sync vs batched

`FileStorage` has two write modes that trade durability for
throughput:

- **Sync (default, `batching=False`).** Every `append_record`
  call acquires the lock, writes, and returns. An fd is cached
  per switch so repeated calls amortize the open/close cost.
  Typical p50 latency on SSD: ~200 µs per append. Durability
  contract: kernel-buffer-durable on return; host-crash-durable
  if `fsync=True`.
- **Batched (`batching=True`).** Appends land in an in-memory
  queue; a background thread drains every `flush_interval_ms`
  (default 50 ms) or when the queue reaches `batch_size`
  (default 64). Typical p50 latency: ~35 µs per append.
  Durability contract: at-risk crash window of up to
  `flush_interval_ms` of tail writes on process death. Call
  `storage.flush()` to drain on demand; `storage.close()` on
  shutdown (registered as an atexit hook automatically).

**`LearnedSwitch(persist=True)`** uses `batching=True` by
default — it's the recommended production path when paired with
`ResilientStorage`. For regulated workloads that need per-call
fsync-strict durability, construct storage explicitly:

```python
from postrule import FileStorage, LearnedSwitch

storage = FileStorage("runtime/postrule", batching=False, fsync=True)
sw = LearnedSwitch(rule=..., storage=storage)
```

`load_records` drains any pending batch before reading, so
reads always see a consistent view regardless of mode.

### Customizing
Subclass `FileStorage` and override:

- `_serialize_line(record) -> bytes` / `_parse_line(bytes) -> Record | None`
  for custom serialization (encryption, compression, schema
  evolution).
- `_rotate(switch_name)` for custom retention policies (time-
  based aging, cold-storage archival before deletion, etc.).

Both hooks are called with the exclusive lock held, so
subclass code doesn't need to re-implement concurrency.

## SqliteStorage

### Guarantees
- **ACID** via WAL journal mode.
- **1 writer + N concurrent readers** within a host. Multiple
  writer processes serialize at SQLite's `BEGIN IMMEDIATE`;
  readers are never blocked.
- **Durability** controlled by `sync` kwarg:
  - `"OFF"` — fastest, host crash can corrupt.
  - `"NORMAL"` (default) — process-crash safe; host crash may
    lose final transaction.
  - `"FULL"` — host-crash safe, ~2× write cost.
- **Graceful degradation:** rows with malformed JSON payloads
  are skipped at load time.
- **Queryable:** ad-hoc SQL against the schema (see below) for
  custom analytics without copying the log.

### Limitations
- **Single-host.** SQLite WAL does not work over NFS / SMB.
- **Not distributed.** For multi-host writers, wait for
  `PostgresStorage` (v0.3+) or bring your own backend.
- **Growth is unbounded** (no rotation / retention). Run
  `VACUUM` periodically on very large logs, or implement a
  retention job in your host.
- **Writer serialization is per-DB-file.** Two processes
  writing simultaneously alternate transactions; throughput is
  ~1 tx/ms under default `NORMAL` on SSD.

### Schema
```sql
CREATE TABLE outcomes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    switch_name  TEXT NOT NULL,
    timestamp    REAL NOT NULL,
    data         TEXT NOT NULL   -- serialized JSON record
);
CREATE INDEX idx_outcomes_switch ON outcomes(switch_name, id);
```
The `data` column uses the canonical format from
`serialize_record()` — identical to a JSONL line in
`FileStorage`. You can `SELECT data FROM outcomes` and feed
the strings through `deserialize_record()` in any host
language.

### Configuration
```python
from postrule import SqliteStorage

storage = SqliteStorage(
    "./runtime/postrule.db",
    sync="NORMAL",    # or "FULL" for host-crash safety
    timeout=30.0,     # seconds to wait on a contended writer
)
```

## ResilientStorage

Wraps any `Storage` backend with an in-memory fallback so that
a transient primary failure does not take down classification.

### Guarantees
- **Classification never blocks on storage I/O.** If the primary
  backend raises on `append_record`, the record spills into a
  `BoundedInMemoryStorage` buffer; classify/dispatch return
  normally.
- **Auto-recovery.** Every `recovery_probe_every` writes in
  degraded mode, the wrapper retries the primary. On success,
  the fallback buffer is drained back to the primary **in
  append order**, then normal operation resumes.
- **Readers get a consistent view.** `load_records()` returns
  primary records followed by fallback records, so the log
  looks chronological even during a degraded episode.
- **Operator signalling.** `degraded` / `degraded_since` /
  `degraded_writes` properties expose state. Optional
  `on_degrade(exception)` and `on_recover(drained_count)`
  callbacks hook into alerting / telemetry.

### Limitations
- **Fallback is volatile.** Records in the in-memory buffer are
  lost on process death. For audit-grade workloads where this
  is unacceptable, use a different strategy (e.g., synchronous
  replication to two storage backends, or hard-fail on I/O
  error so the operator notices immediately).
- **Degraded mode can mask a persistent failure.** If the
  primary is permanently broken (bad config, permissions,
  corrupted filesystem), the wrapper will keep retrying
  forever. Watch `on_degrade` and `degraded_since` for
  operator escalation.
- **Fallback buffer is capped.** Default 100 000 records; once
  full, FIFO eviction kicks in and oldest fallback records are
  lost. Raise the cap for longer outages at the cost of RAM.

### Configuration
```python
from postrule import ResilientStorage, FileStorage

storage = ResilientStorage(
    FileStorage("./runtime/postrule"),
    fallback_max_records=100_000,     # cap on in-memory buffer
    recovery_probe_every=100,          # writes between recovery attempts
    on_degrade=lambda exc: alerts.emit("postrule.degraded", error=str(exc)),
    on_recover=lambda n: alerts.emit("postrule.recovered", drained=n),
)
```

### How `persist=True` uses it
```python
# These two are equivalent:
switch = LearnedSwitch(rule=..., persist=True)
switch = LearnedSwitch(
    rule=...,
    storage=ResilientStorage(FileStorage("runtime/postrule")),
)
```
If you want the bare primary with no fallback (hard-fail on
I/O error — useful for audit-grade strict mode):
```python
switch = LearnedSwitch(rule=..., storage=FileStorage("runtime/postrule"))
```

### When NOT to use
- **Regulated / audit-grade classifications** where silent
  degradation from durable to volatile storage is a compliance
  violation. Fail loudly instead: pass the primary directly
  and let the caller handle I/O errors.
- **Workloads that already have strong durability upstream**
  (e.g., classifying messages off a Kafka queue with at-least-
  once semantics — the queue replays losses, so
  classification retry is the correct recovery, not a
  fallback buffer).

## Choosing a backend: decision tree

```
Single process, short-lived?
├── YES → BoundedInMemoryStorage (default). Done.
└── NO  → Multiple processes writing?
         ├── NO  → Need durability?
         │        ├── NO  → BoundedInMemoryStorage.
         │        └── YES → FileStorage (or persist=True).
         └── YES → Single host?
                  ├── YES → SqliteStorage.    ← recommended prod
                  └── NO  → PostgresStorage (v0.3+) or custom backend.
```

## Writing a custom backend

Two options, same contract.

### Option A — duck-typed (`Storage` protocol)
Any object with `append_record(switch_name, record)` and
`load_records(switch_name) -> list[ClassificationRecord]` is a
valid backend. No inheritance required.

```python
from postrule import ClassificationRecord, serialize_record, deserialize_record

class StdoutStorage:
    """Toy backend: log predictions to stdout and never read back."""

    def append_record(self, switch_name, record):
        print(f"[{switch_name}] {serialize_record(record)}")

    def load_records(self, switch_name):
        return []

# Works without inheriting anything:
switch = LearnedSwitch(name="triage", rule=rule, storage=StdoutStorage())
```

### Option B — ABC (`StorageBase`)
Prefer inheritance for abstract-method enforcement? Subclass
`StorageBase`:

```python
from postrule import StorageBase, serialize_record, deserialize_record
import boto3

class S3Storage(StorageBase):
    def __init__(self, bucket, prefix):
        self._client = boto3.client("s3")
        self._bucket = bucket
        self._prefix = prefix

    def append_record(self, switch_name, record):
        key = f"{self._prefix}/{switch_name}.jsonl"
        line = serialize_record(record) + "\n"
        # Naive append — in production you'd batch / use multipart.
        existing = self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()
        self._client.put_object(
            Bucket=self._bucket, Key=key, Body=existing + line.encode()
        )

    def load_records(self, switch_name):
        key = f"{self._prefix}/{switch_name}.jsonl"
        raw = self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()
        out = []
        for line in raw.decode().splitlines():
            if not line.strip():
                continue
            try:
                out.append(deserialize_record(line))
            except (ValueError, TypeError):
                continue  # Skip malformed rows, match FileStorage semantics.
        return out
```

Attempting to instantiate `StorageBase` directly (or a subclass
missing one of the abstract methods) raises `TypeError` at
construction.

### Helpers — use these, don't re-invent

```python
from postrule import serialize_record, deserialize_record

# Encode a record → single JSON line (no trailing newline).
line = serialize_record(record)

# Decode one line → record. Raises on malformed input so you can
# branch on parse errors in your custom backend.
rec = deserialize_record(line)
```

Both helpers are stable across Postrule versions within the same
major release. Breaking changes are called out in
`CHANGELOG.md`.

### Invariants a well-behaved backend must preserve

1. **Append order** — `load_records()` returns records in the
   same order they were appended (oldest first). Phase-
   transition math depends on this.
2. **No silent drops on durability paths.** If you advertise
   durability, back it up (flush / fsync / ACK). If you can't,
   say so in the backend's docstring.
3. **Read-during-write tolerance.** Readers should either
   return a consistent (possibly stale) snapshot, or degrade
   gracefully to skipping malformed entries. They should never
   crash.

## When to reach outside Postrule's built-ins

Use a custom backend (or the upcoming `PostgresStorage`) when:

- You need **cross-host durability** — multiple writer machines
  sharing one log. No built-in does this today.
- You need **time-based retention** (regulatory "delete after N
  days" instead of "keep last M bytes"). Subclass `FileStorage`
  and override `_rotate`, or use a custom DB backend.
- You need **encryption at rest** beyond filesystem / DB
  encryption. Subclass `FileStorage` and wrap `_serialize_line`
  / `_parse_line` with your cipher.
- You need **structured queryability** beyond SQLite — e.g.,
  partitioned time-series analytics. Use a custom backend
  that feeds your analytics store.

## Roadmap (v0.3+)

- `PostgresStorage` for distributed writers. Same `StorageBase`
  contract; uses `psycopg` (optional install extra).
- Optional **compression** on `FileStorage` rotated segments.
- **Time-based retention** policy as a first-class config knob.
- **Async** variant of the `Storage` protocol for async hosts.

See the project roadmap for the concrete schedule.
