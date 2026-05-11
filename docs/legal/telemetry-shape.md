# Telemetry wire-format specification

> **Last updated 2026-05-11.** Source of truth:
> [`src/dendra/cloud/verdict_telemetry.py`](../../src/dendra/cloud/verdict_telemetry.py).
> Companion to the rendered
> [Privacy Policy](../../cloud/dashboard/app/privacy/page.tsx) and
> [`dpa-template.md`](dpa-template.md) §5.

This page is the engineer-buyer's audit artifact. It states what the
SDK sends, what it does not send, how often, under what auth, and how
to verify both claims locally.

The implementation in `verdict_telemetry.py` is authoritative; this
page tracks it. If they disagree, the code wins and the page is a
bug.

---

## 1. Overview

The Dendra SDK emits at most one HTTP request per
`record_verdict()` call. The request carries a single Telemetry Event
describing the outcome of one classification. The event contains
metering and paired-correctness information; it does **not** contain
inputs, labels, or anything derived from inputs or labels.

The SDK installs the telemetry sender at import time if **and only
if** both of the following are true:

1. The user has signed in (`~/.dendra/credentials` is present and
   contains an `api_key`).
2. None of the three opt-out paths are active. See §3.

When either condition fails, no telemetry sender is registered and
the SDK makes no telemetry-related network calls. The decorator
operates entirely in-process.

### 1.1 The three opt-out paths

Any one of these silences telemetry; whichever is most restrictive
wins.

- **Environment variable.** `DENDRA_NO_TELEMETRY=1` (or any of the
  truthy spellings `true`/`yes`/`on`/non-`0`/non-`false`/non-`off`)
  in the process environment short-circuits the installer.
- **Per-switch.** Passing `telemetry=NullEmitter()` to
  `@ml_switch(…)` or `LearnedSwitch(…)` disables emission on that
  switch only.
- **Account.** Toggling telemetry off on
  `/dashboard/settings`. The setting is cached locally in
  `~/.dendra/credentials` at `dendra login` time and refreshed
  against `GET /v1/whoami` opportunistically.

The three are checked independently; opt-out by any one of them
skips the network send entirely. There is no "still record but
ignore" path.

---

## 2. Wire format

Exact JSON shape of a Telemetry Event, as constructed by
[`CloudVerdictEmitter._build_payload`](../../src/dendra/cloud/verdict_telemetry.py)
and serialised by `json.dumps(payload, default=str,
separators=(",", ":"))`:

```json
{
  "switch_name": "ticket_priority",
  "request_id":  "8f3c1d40a3624b5d8a9b6c1e0d2f3a4b",
  "phase":       "P3",
  "rule_correct":  true,
  "model_correct": false,
  "ml_correct":    true
}
```

### 2.1 Field semantics

| Field            | Type                          | Always present | Source         | What this could leak                                                                                                                                          |
|------------------|-------------------------------|----------------|----------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `switch_name`    | `string`                      | Yes            | Customer-chosen | Whatever the customer's developer wrote in the `@ml_switch(name=…)` argument or the decorated function name. The SDK has no technical means to detect when this string is sensitive; customers are responsible for not naming a switch in a way that reveals Personal Data, secrets, or contractual identifiers. |
| `request_id`     | `string` (UUIDv4 hex)         | Yes            | SDK-generated  | Nothing. Used for idempotent retry deduplication server-side. Not linkable across requests.                                                                  |
| `phase`          | `"P0" \| "P1" \| … \| "P5"`   | Optional       | Switch state   | The lifecycle phase the switch was in when the verdict was recorded. Aggregated over many events this reveals the customer's adoption curve; per event it reveals nothing about content. |
| `rule_correct`   | `bool`                        | Optional       | Verdict        | Whether the rule's prediction was judged correct, for this single example. Three independent booleans across `rule_correct` / `model_correct` / `ml_correct` describe the agreement matrix at this site. |
| `model_correct`  | `bool`                        | Optional       | Verdict        | As above.                                                                                                                                                     |
| `ml_correct`     | `bool`                        | Optional       | Verdict        | As above.                                                                                                                                                     |

A field is absent when its source value is `None`. The `_build_payload`
function constructs the dict from a fixed allow-list; new fields are
not added by accident.

### 2.2 What the server attaches on receipt

The hosted API attaches the following on receipt and stores them with
the event. **These are not on the wire from the SDK.**

| Field          | Type     | Source                                                                                                       |
|----------------|----------|--------------------------------------------------------------------------------------------------------------|
| `account_hash` | `string` | Derived from the bearer token on the request: HMAC-SHA-256 of the signing user's email under a server-side pepper. Pseudonymous; the pepper is not exported. |
| `received_at`  | `string` | UTC ISO-8601 timestamp assigned by the API.                                                                  |

The SDK does not compute `account_hash` and does not include it in the
payload; the server's bearer-token lookup binds the event to an
account.

---

## 3. HTTP request shape

```
POST /v1/verdicts HTTP/1.1
Host: api.dendra.run
Authorization: Bearer dndr_live_<32 base62 chars>
Content-Type: application/json
User-Agent: dendra-sdk-verdicts/1.0

<JSON body from §2>
```

- **Method.** `POST`.
- **Endpoint.** `https://api.dendra.run/v1/verdicts`. Overridable
  with `$DENDRA_API_URL`.
- **Transport.** TLS only. The SDK constructs requests through
  `urllib.request.Request`; non-HTTPS endpoints would be rejected by
  the hosted API. For local development, an HTTP endpoint may be
  configured via `$DENDRA_API_URL=http://localhost:8787`.
- **Authorisation.** Bearer token. The token is a 190-bit random
  string issued at `dendra login`; the server stores only an
  HMAC-SHA-256 hash of it under a server-side pepper (see
  [`src/dendra/../cloud/api/src/keys.ts`](../../cloud/api/src/keys.ts)).
- **Content-Type.** `application/json`. Body is the compact JSON
  serialisation in §2.
- **Timeout.** `5.0` seconds per request (`REQUEST_TIMEOUT_SECONDS`).
- **Retry policy.** None. A failed request is dropped. The server's
  `duplicate=True` return covers the case where the network ack was
  lost and the SDK had already retried; the SDK itself does not
  retry.
- **Failure mode.** Silent. Any non-2xx response, transport error,
  timeout, or DNS failure is absorbed by the sender thread. The
  calling code path — `record_verdict`, the decorator, the
  dispatcher — is never blocked or raised-into by telemetry.

---

## 4. What is NOT sent

The fields below are **not** on the wire under any telemetry mode,
including the cohort-enrolment opt-in path. Each item is named so a
reviewer can confirm its absence with the `_build_payload`
inspection in §7.

| Field                              | Why it is not sent                                                                                                                              |
|------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------|
| Classifier input text              | The input is the customer's data. It stays on the customer's machine.                                                                            |
| Classifier output label            | The label is the customer's data. It stays on the customer's machine.                                                                            |
| Ground-truth label                 | The label is the customer's data.                                                                                                                |
| Prompt text sent to an LLM judge   | The judge's prompt is constructed from the customer's input and is never serialised into the telemetry payload.                                  |
| Switch dataset metadata            | Length, language, fingerprint, embedding, hashing-derived signal — none are computed at emit time and none are in the allow-list of `_build_payload`. |
| Environment variables              | The SDK reads `DENDRA_API_URL`, `DENDRA_NO_TELEMETRY`, and the auth cache; it serialises none of them.                                           |
| Host information                   | Hostname, machine ID, MAC, OS version, CPU info — none are collected.                                                                            |
| Client IP                          | The SDK does not include the client IP in the payload. The hosting Sub-processor (Cloudflare) sees the IP at the edge; the Processor does not store the IP against the Telemetry Event. Operational logs retain IPs for 24 hours and then redact. |
| Per-call latency                   | Not measured in the payload-construction path; not included.                                                                                     |
| Per-call cost                      | Not measured; not included.                                                                                                                      |
| Error messages and stack traces    | When the verdict-recording code raises, the exception propagates to the caller. The telemetry payload describes a verdict outcome, not a fault; faults are not telemetered to the hosted API. |

The negative list is enforced by construction: `_build_payload` only
copies fields from a fixed allow-list (`switch_name`, `request_id`,
`phase`, `rule_correct`, `model_correct`, `ml_correct`). Adding a
new field to the wire requires editing that function. The function
is in a BSL-1.1 file enforced by the license-check workflow; the
allow-list cannot be silently widened.

---

## 5. Rate limit and queue

The SDK protects itself and the hosted API from runaway emission
without dropping the calling code path's hot loop.

- **Token bucket.** Per process. Capacity 200 tokens
  (`RATE_LIMIT_BURST`), refill 100 tokens / second
  (`RATE_LIMIT_PER_SECOND`). A token is consumed before the payload
  is enqueued; over-budget events are dropped at emit time and
  counted in `CloudVerdictEmitter.dropped_rate_limited`.
- **Bounded queue.** Capacity 1,024 events (`QUEUE_CAPACITY`). On
  overflow, the SDK drops the **oldest** event in the queue and
  enqueues the new one. The trade-off is that dashboards stay
  current while the worst-case memory footprint of the queue is
  bounded.
- **Sender thread.** A single daemon thread per process consumes the
  queue and `POST`s each event with the timeout in §3. Failures are
  silent (§3).

Counters exposed on the emitter instance for runtime introspection:

| Counter                    | What it counts                                                          |
|----------------------------|--------------------------------------------------------------------------|
| `queued`                   | Events enqueued for sending.                                             |
| `sent`                     | Events for which the server returned 2xx.                                |
| `dropped_rate_limited`     | Events dropped because the token bucket was empty.                       |
| `dropped_queue_full`       | Events dropped because the queue was full (drop-oldest).                 |
| `failed`                   | Events sent but for which the server returned a non-2xx or transport failed. |

---

## 6. Retention

Telemetry Events are retained for the window associated with the
account's tier. Per
[`dpa-template.md`](dpa-template.md) §8:

| Tier     | Retention window |
|----------|------------------|
| Free     | 7 days           |
| Pro      | 30 days          |
| Scale    | 90 days          |
| Business | 1 year           |

After the window closes the event is hard-deleted from the primary
data store. The single exception is the de-linked-from-`account_hash`
cohort contribution described in [`dpa-template.md`](dpa-template.md)
§8.2, which is retained without the account linkage and therefore no
longer constitutes Personal Data.

---

## 7. Verifying the contract programmatically

You can verify what would leave your machine without running the
sender. The `_build_payload` function is the audit point.

```python
from dendra.cloud.verdict_telemetry import CloudVerdictEmitter

emitter = CloudVerdictEmitter(
    api_url="https://example.invalid",
    bearer_token="dndr_live_x" + "0" * 31,
    start_thread=False,
)

# Construct an outcome event the same way record_verdict() does:
ev = {
    "switch": "ticket_priority",
    "phase": "P3",
    "rule_correct": True,
    "model_correct": False,
    "ml_correct": True,
    # The next three fields exist in the in-process telemetry event
    # but are NOT copied to the wire payload by _build_payload:
    "input": "this is the user's classifier input",
    "label": "this is the customer's chosen label",
    "ground_truth": "this is the customer's reference label",
}

print(emitter._build_payload(ev))
# → {'switch_name': 'ticket_priority',
#    'request_id': '<uuid4>',
#    'phase': 'P3',
#    'rule_correct': True,
#    'model_correct': False,
#    'ml_correct': True}
```

Note the absence of `input`, `label`, and `ground_truth`. The
allow-list construction in `_build_payload` is the mechanism: it
copies only the named fields and ignores everything else in the
incoming event. A test that asserts this is the right shape to land
in your own pre-flight CI:

```python
import pytest
from dendra.cloud.verdict_telemetry import CloudVerdictEmitter

@pytest.fixture
def emitter():
    return CloudVerdictEmitter(
        api_url="https://example.invalid",
        bearer_token="dndr_live_x" + "0" * 31,
        start_thread=False,
    )

def test_payload_does_not_leak_inputs_or_labels(emitter):
    ev = {
        "switch": "ticket_priority",
        "phase": "P3",
        "rule_correct": True,
        "input": "customer payload",
        "label": "customer label",
        "ground_truth": "customer ground truth",
        "prompt": "anything",
        "metadata": {"any": "thing"},
    }
    payload = emitter._build_payload(ev)
    assert payload is not None
    forbidden = {"input", "label", "ground_truth", "prompt", "metadata"}
    assert not (forbidden & payload.keys())
    assert payload.keys() <= {
        "switch_name", "request_id", "phase",
        "rule_correct", "model_correct", "ml_correct",
    }
```

The test exercises the same allow-list construction the production
sender uses. Pin it in your CI and you have a regression-time
guarantee that no Dendra upgrade can widen the wire shape without
your test failing.

---

## 8. Auditing emissions live

To watch what your process actually sends:

### 8.1 Local stub

Point the SDK at a local stub. Anything it tries to send appears in
the stub's request log; anything missing from the log is something
the SDK did not send.

```bash
# Terminal 1 — a one-file stub that logs every POST it receives.
python -m http.server 8787 --bind 127.0.0.1 &  # for quick traffic check
# (or run a small Flask/aiohttp stub that prints request bodies for
#  payload inspection)

# Terminal 2 — run your code against the stub.
export DENDRA_API_URL=http://localhost:8787
python your_app.py
```

The Dendra SDK reads `DENDRA_API_URL` at `maybe_install` time and
sends every Telemetry Event to it. Inspect the request body to
confirm it matches §2.

### 8.2 Packet capture

If you'd rather watch the wire directly:

```bash
# Capture outbound traffic to the hosted endpoint.
sudo tcpdump -i any -A -s 0 'host api.dendra.run and tcp port 443'
```

You will see TLS handshakes, not plaintext bodies — which is the
point. To inspect bodies, route through a local TLS-terminating
proxy you control (`mitmproxy`, `proxyman`, similar) and confirm the
request bodies match §2.

---

## 9. Versioning and stability

9.1 **Contract stability.** The wire shape in §2 is a published
contract. The Processor will **not** add a field to the payload
without:

- a **90-day deprecation notice** published in this document, in the
  CHANGELOG, and (for opt-out-eligible customers) by email to the
  billing contact on file;
- a corresponding **SDK major-version bump**. Customers pinned to
  the prior major SDK version see no expansion until they upgrade
  deliberately.

9.2 **Removing a field.** Removal of a field never widens the wire
shape and follows the SDK's standard semver deprecation policy
(deprecate at major N, remove at major N+1).

9.3 **What the 90-day notice covers.** A "field" for the purpose of
§9.1 is any name that would appear at the top level of the JSON
payload or as a nested key of an object included at the top level.
The list of names that count is the union of §2.1 and §2.2.

9.4 **The opt-out is the floor.** Even after a documented expansion,
all three opt-out paths in §1.1 continue to work. A customer who has
opted out at any of the three levels will never see a Telemetry
Event leave their machine.

---

*Last updated 2026-05-11. Source of truth is the code at
[`src/dendra/cloud/verdict_telemetry.py`](../../src/dendra/cloud/verdict_telemetry.py).
If this page and the code disagree, the code is right.*
