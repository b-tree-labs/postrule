# Verdict sources

A **VerdictSource** is the "where does truth come from" seam.
The switch classifies; a `VerdictSource` judges whether the
classification was correct. The verdict lands in the outcome log
with a stable source stamp the audit chain can filter on.

Postrule ships five built-in sources and the `VerdictSource`
protocol so you can add your own.

## Decision matrix

| Source | Use when | Bias risk | Latency |
|---|---|---|---|
| [`CallableVerdictSource`](#callableverdictsource) | Truth is computable locally — downstream signal, DB lookup, business rule. | None (deterministic code). | Callable-bound. |
| [`JudgeSource`](#llmjudgesource) | A distinct language model can critique the classifier's output. | **Medium** — mitigated by the self-judgment guardrail. | One model call per verdict. |
| [`JudgeCommittee`](#llmcommitteesource) | Multiple distinct language models; willing to pay N× cost for tighter bias control. | Low under majority / unanimous aggregation. | N model calls per verdict. |
| [`WebhookVerdictSource`](#webhookverdictsource) | External system (CRM, fraud, payments) can report outcomes on demand. | External-system-dependent. | HTTP round-trip per verdict. |
| [`HumanReviewerSource`](#humanreviewersource) | A human in the loop produces the ground-truth signal. | None (if reviewers are disciplined). | Queue-bound; timeout → UNKNOWN. |

For cold-start preloads and periodic reviewer rounds,
:meth:`LearnedSwitch.bulk_record_verdicts` and
:meth:`bulk_record_verdicts_from_source` amortize the
per-verdict overhead — one lock acquisition, one gate evaluation
at end-of-batch.

## Audit-chain stamping

Every `VerdictSource` exposes a stable `source_name` string:

- `CallableVerdictSource(fn, name="oracle")` → `callable:oracle`
- `JudgeSource(GPT)` → `llm-judge:gpt-4o-mini`
- `JudgeCommittee([A, B, C], mode="majority")` → `llm-committee:A|B|C(majority)`
- `WebhookVerdictSource(endpoint, name="crm")` → `webhook:crm`
- `HumanReviewerSource(name="ops-team")` → `human-reviewer:ops-team`

Verdicts routed through
`bulk_record_verdicts_from_source(inputs, source)` persist with
`record.source = source.source_name`. Filters like "only
human-verified verdicts" become a simple
`r.source.startswith("human-reviewer:")` scan of the outcome log.

## Self-judgment bias guardrail

Using the same language model as both classifier and judge is a
well-documented failure mode
([G-Eval, NAACL 2023](https://arxiv.org/abs/2303.16634);
[MT-Bench, NeurIPS 2023](https://arxiv.org/abs/2306.05685);
Chatbot Arena, ICML 2024). The same model agrees with its own
outputs even when wrong, biasing verdicts toward the
classifier's own errors.

Both `JudgeSource` and `JudgeCommittee` accept
`require_distinct_from=<classifier>`. At construction, Postrule
checks `judge is classifier` **and** `(class_name,
model_string)` — which catches the "two separate
`OpenAIAdapter(model='gpt-4o-mini')` instances" case. If they
resolve to the same language model, construction raises `ValueError` with
a reference to the literature. Pass
`guard_against_same_llm=False` only when the caller explicitly
accepts the bias risk and has their own mitigation.

## Implementations

### CallableVerdictSource

```python
from postrule import CallableVerdictSource, Verdict

def oracle(input, label):
    return Verdict.CORRECT if label == _ground_truth_for(input) else Verdict.INCORRECT

src = CallableVerdictSource(oracle, name="oracle")
verdict = src.judge(input, label)
```

Any `(input, label) -> Verdict` callable works. Return value
must be a `Verdict` instance — string values raise `TypeError`
so a mistyped return doesn't silently poison the log.

### JudgeSource

```python
from postrule import OpenAIAdapter, AnthropicAdapter, JudgeSource

classifier = OpenAIAdapter(model="gpt-4o-mini")
judge_model = AnthropicAdapter(model="claude-haiku-4-5")

judge = JudgeSource(judge_model, require_distinct_from=classifier)
verdict = judge.judge(input, classifier_label)
```

Judge-side failures (network error, rate-limit, parse error)
absorb to `Verdict.UNKNOWN` — an outage on the critic never
breaks the caller's audit loop.

### JudgeCommittee

```python
from postrule import JudgeCommittee

committee = JudgeCommittee(
    [OpenAIAdapter(model="gpt-4o-mini"),
     AnthropicAdapter(model="claude-haiku-4-5"),
     OllamaAdapter(model="qwen2.5:7b")],
    mode="majority",  # or "unanimous"
    require_distinct_from=classifier,
)
```

Modes:

- **`majority`**: plurality wins; ties → UNKNOWN. Odd committee
  sizes (3, 5, 7) are stable.
- **`unanimous`**: all judges must agree on a non-UNKNOWN
  verdict; any disagreement → UNKNOWN. Use when false positives
  are expensive (medical, irreversible actions).
- **`confidence_weighted`**: reserved for future extension; v1
  currently falls through to majority semantics. Callers can
  pin this name today and upgrade without code changes.

### WebhookVerdictSource

```python
from postrule import WebhookVerdictSource
import os

src = WebhookVerdictSource(
    "https://crm.example.com/api/v1/ticket-verdicts",
    headers={"X-API-Key": os.environ["CRM_API_KEY"]},
    timeout=10.0,
    name="crm",
)
```

Contract the external endpoint must honor:

```
POST <endpoint>
body:     {"input": <classifier input>, "label": <classified label>}
response: {"outcome": "correct" | "incorrect" | "unknown"}  (HTTP 200)
```

All failure modes (connection error, timeout, non-2xx,
malformed JSON, unknown outcome value) collapse to
`Verdict.UNKNOWN`.

For webhook-push (external system POSTs to **you**), accept the
push in your own HTTP route and call
`switch.record_verdict` or `switch.apply_reviews` directly
instead of wiring a `WebhookVerdictSource`.

### HumanReviewerSource

```python
from postrule import HumanReviewerSource
import queue

pending = queue.Queue()
verdicts = queue.Queue()

src = HumanReviewerSource(
    pending=pending,
    verdicts=verdicts,
    timeout=300.0,  # 5-minute max wait
    name="ops-team",
)
```

The reviewer tool (web UI, Slack bot, CLI) pops `(input, label)`
tuples from `pending` and pushes a `Verdict` (or string
`"correct"` / `"incorrect"` / `"unknown"`) onto `verdicts`.

For production, subclass and override `_push` / `_pop_verdict`
to route through Redis, SQS, Kafka, or your reviewer tool's
webhook:

```python
class RedisReviewerSource(HumanReviewerSource):
    def __init__(self, redis_client, *, stream="reviews", **kwargs):
        super().__init__(**kwargs)
        self._redis = redis_client
        self._stream = stream

    def _push(self, input, label):
        self._redis.xadd(f"{self._stream}:pending", {"input": ..., "label": ...})

    def _pop_verdict(self):
        # blpop with timeout, parse payload, return Verdict
        ...
```

## Custom sources

Implement the `VerdictSource` protocol on any object:

```python
from postrule import Verdict, VerdictSource

class MyCustomSource:
    source_name = "custom:my-pipeline"

    def judge(self, input, label):
        # your logic
        return Verdict.CORRECT

assert isinstance(MyCustomSource(), VerdictSource)  # runtime_checkable
```

The protocol is `runtime_checkable`, so `isinstance(obj,
VerdictSource)` works without inheritance. Keep `source_name`
stable across versions of your source — the audit-chain filters
depend on it.

## See also

- [`docs/api-reference.md`](api-reference.md) — full public API
  surface.
- [`examples/11_llm_judge.py`](../examples/11_llm_judge.py),
  [`12_llm_committee.py`](../examples/12_llm_committee.py),
  [`13_webhook_verdicts.py`](../examples/13_webhook_verdicts.py),
  [`14_human_reviewer_queue.py`](../examples/14_human_reviewer_queue.py)
  — runnable demonstrations.
- [`examples/10_bulk_verdict_ingestion.py`](../examples/10_bulk_verdict_ingestion.py)
  — bulk ingestion patterns including `bulk_record_verdicts_from_source`.
