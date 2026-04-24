# Dendra examples

Runnable examples. Each file is self-contained — no external
services, no API keys — and targets a single conceptual piece of
the Dendra primitive.

```bash
pip install git+https://github.com/axiom-labs-os/dendra
python examples/01_hello_world.py
```

New to Dendra? Start with [`01_hello_world.py`](./01_hello_world.py),
then skim [`../docs/api-reference.md`](../docs/api-reference.md) for
the minimum required vs optional surface.

## Gallery

| # | File | What it shows |
|---|---|---|
| 1 | [`01_hello_world.py`](./01_hello_world.py) | Smallest complete example: dict-labels + dispatch. |
| 2 | [`02_outcome_log.py`](./02_outcome_log.py) | `persist=True` + ground-truth verdicts; reading the log back. The outcome log feeds phase graduation, ROI, drift. |
| 3 | [`03_safety_critical.py`](./03_safety_critical.py) | `safety_critical=True` refuses construction in `Phase.ML_PRIMARY` — the rule floor is architecturally guaranteed. |
| 4 | [`04_llm_shadow.py`](./04_llm_shadow.py) | `Phase.MODEL_SHADOW`: rule decides, LLM observes; paired predictions land in the outcome log for later transition-gate analysis. Stub LLM (swap for `OpenAIAdapter` / `AnthropicAdapter` / `OllamaAdapter` / `LlamafileAdapter` in production). |
| 5 | [`05_output_safety.py`](./05_output_safety.py) | Same primitive on LLM *output* — PII / confidentiality gating. `list[str]` labels (no dispatch; caller handles). |
| 6 | [`06_ml_primary.py`](./06_ml_primary.py) | End-state: `Phase.ML_PRIMARY` with a healthy ML head deciding and the rule as circuit-breaker target. Part 2 simulates an ML failure → breaker trip → operator reset. |
| 7 | [`07_llm_as_teacher.py`](./07_llm_as_teacher.py) | Cold-start: start at `Phase.MODEL_PRIMARY` with zero labeled data, let the LLM label production traffic, then train a local ML head and graduate (operator-triggered). |
| 8 | [`08_classify_vs_dispatch.py`](./08_classify_vs_dispatch.py) | The two verbs: `classify()` pure (tests / dashboards); `dispatch()` classify + fire the handler. Includes the graceful-failure contract — a handler that raises is captured on `action_raised`, not propagated. |
| 9 | [`09_verdict_webhook.py`](./09_verdict_webhook.py) | Verdicts arriving async from outside the process (simulated reviewer thread feeding a queue). Shows the three ergonomic shapes — direct `record_verdict`, fluent `.mark_correct()`, and `verdict_for()` context manager — plus the `on_verdict=` mirror-to-audit hook. |

## On the roadmap

Not yet written — contributions welcome via
[`../CONTRIBUTING.md`](../CONTRIBUTING.md):

- End-to-end transition curve on a public benchmark (ATIS-class).
- `dendra roi` report from an accumulated outcome log.
- Integration with LangSmith / Weights & Biases telemetry.
- Vision / audio / multimodal adapters.

## License

Examples are licensed under **Apache 2.0** (matching the client
SDK they exercise). The `SPDX-License-Identifier: Apache-2.0`
header at the top of each file declares this explicitly. You
can copy these files into your own projects without attribution
beyond the Apache 2.0 license terms.
