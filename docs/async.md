# Async API

Every sync method on :class:`LearnedSwitch` has an ``a``-prefixed
async peer. The same `SwitchConfig`, the same storage, the same
locks — just a coroutine surface so async callers (FastAPI,
Starlette, LangGraph, LlamaIndex, any event-loop-driven runtime)
can `await` classification without burning a threadpool worker.

Async is additive. Sync and async on the same switch is
supported; internal state is shared; the `threading.RLock` on
`LearnedSwitch` protects both entry points.

## Surface

| Sync | Async | Notes |
|---|---|---|
| `classify(input)` | `aclassify(input)` | Default: wraps sync via `asyncio.to_thread`. |
| `dispatch(input)` | `adispatch(input)` | Same. Action itself is still sync. |
| `record_verdict(...)` | `arecord_verdict(...)` | Same. |
| `bulk_record_verdicts(batch)` | `abulk_record_verdicts(batch)` | Same. |
| `bulk_record_verdicts_from_source(inputs, source)` | `abulk_record_verdicts_from_source(inputs, source)` | Native-async path when `source.ajudge` exists; wraps sync otherwise. |

## When to use which

- **Async (`a*`)**: inside a FastAPI / LangGraph / LlamaIndex
  route, an asyncio task graph, or any event-loop-driven code.
  Yields the loop during any underlying I/O.
- **Sync**: inside a threadpool-driven web framework (Flask,
  Django sync views), batch scripts, notebooks, CLI tooling.
  Simpler semantics; no `await` chain.

Mixing is allowed. Interior code that's heavy on model calls can
use the async path even when the outer caller is sync (via
`asyncio.run`). A long-running sync batch can call
`await self.acl assify(...)` from a nested event loop.

## Adapter siblings

Every shipped language-model adapter has an async peer using the provider's
native async client:

| Sync | Async | Native client |
|---|---|---|
| `OpenAIAdapter` | `OpenAIAsyncAdapter` | `openai.AsyncOpenAI` |
| `AnthropicAdapter` | `AnthropicAsyncAdapter` | `anthropic.AsyncAnthropic` |
| `OllamaAdapter` | `OllamaAsyncAdapter` | `httpx.AsyncClient` |
| `LlamafileAdapter` | `LlamafileAsyncAdapter` | thin wrapper on `OpenAIAsyncAdapter` |

Async adapters expose `aclassify(input, labels)` (coroutine)
instead of `classify(input, labels)`. Use them with
`JudgeSource` / `JudgeCommittee` — both sources detect
`aclassify` automatically and dispatch through the async path on
`ajudge` calls.

## The big win: parallel committee judges

```python
import asyncio

from postrule import (
    OpenAIAsyncAdapter,
    AnthropicAsyncAdapter,
    OllamaAsyncAdapter,
    JudgeCommittee,
)

committee = JudgeCommittee(
    [
        OpenAIAsyncAdapter(model="gpt-4o-mini"),
        AnthropicAsyncAdapter(model="claude-haiku-4-5"),
        OllamaAsyncAdapter(model="qwen2.5:7b"),
    ],
    mode="majority",
)

async def judge_ticket(ticket, label):
    return await committee.ajudge(ticket, label)
```

`ajudge` uses `asyncio.gather` — all three judges fire in
parallel. Committee latency is `max(judge_latencies)`, not
`sum(judge_latencies)`. Scales linearly with committee size
instead of linearly with the number of judges × their latency.

## Custom VerdictSource: adding `ajudge`

Any object satisfying the `VerdictSource` protocol (has
`judge(input, label)`) is passed through as-is. To ship native
async behavior, add an `ajudge` coroutine alongside `judge`:

```python
class MyAsyncReviewerSource:
    source_name = "custom:my-reviewer"

    def judge(self, input, label):
        # sync fallback — called from sync bulk paths
        ...

    async def ajudge(self, input, label):
        # async path — called from abulk_record_verdicts_from_source
        async with httpx.AsyncClient() as client:
            r = await client.post(...)
        ...
```

`LearnedSwitch.abulk_record_verdicts_from_source` detects
`ajudge` and skips the `asyncio.to_thread` hop. Sources without
`ajudge` still work via the sync fallback — pay one thread hop
per verdict.

## Storage and the async API

The shipped storage backends (`BoundedInMemoryStorage`,
`FileStorage`, `SqliteStorage`, `ResilientStorage`) are all
sync. Under the async API, storage writes happen on a worker
thread via `asyncio.to_thread` — correct and bounded, but not
zero-overhead.

For a native-async storage backend (on top of `aiofiles` /
`aiosqlite`), subclass `LearnedSwitch` and override
`aclassify` / `adispatch` / `arecord_verdict` to route through
your async storage directly. The hooks are small (dispatch to
`_classify_impl`, adjust the storage write site); the
protocol invariants and telemetry events survive.

## Examples

- [`examples/15_async_fastapi.py`](../examples/15_async_fastapi.py)
  — FastAPI route with `await sw.aclassify(...)`, reviewer
  roundtrip endpoint, and a sync `/status` handler for
  low-cost reads.
- [`examples/16_async_committee.py`](../examples/16_async_committee.py)
  — `asyncio.gather` across an async committee showing the
  serial-vs-parallel latency delta (3× on a 3-judge committee).

## Interop guarantees

- Sync and async on the same switch share state. Locks protect
  both entry points.
- Auto-advance fires from whichever entry point hits the
  interval boundary — there is no per-entry-point counter.
- Telemetry events are the same shape regardless of entry
  point. No `async:`-prefixed event names.
- `config.gate` runs in-process (sync). A custom async gate
  would subclass `LearnedSwitch.advance` + `aadvance`; none of
  the shipped gates need async.

## What's not in v1

- **Async-native storage backends** — v1.1 follow-up. `aiofiles`
  and `aiosqlite` peers of `FileStorage` / `SqliteStorage`.
- **Async telemetry emitters** — sync for v1. Webhook-to-OTel
  emitters can still be written sync; they just pay the
  thread-hop on async paths.
- **Streaming verdict sources** — `async for v in
  source.stream()` pattern reserved on the roadmap for
  `WebhookVerdictSource` push-push ingestion.
