# Dendra examples

Runnable examples. Each file is self-contained — no external
services, no API keys — and targets a single conceptual piece of
the Dendra primitive.

```bash
pip install dendra
python examples/01_hello_world.py
```

## Gallery

| # | File | What it shows |
|---|---|---|
| 1 | [`01_hello_world.py`](./01_hello_world.py) | The smallest possible `@ml_switch` wrap. Rule-only phase. Behavior is identical to the un-wrapped rule. |
| 2 | [`02_outcome_log.py`](./02_outcome_log.py) | Recording outcomes into `InMemoryStorage`; reading them back; computing rule accuracy. The outcome log is what feeds phase-transition decisions later. |
| 3 | [`03_safety_critical.py`](./03_safety_critical.py) | `safety_critical=True` refuses construction in `Phase.ML_PRIMARY`. The rule floor is architecturally guaranteed for authorization-class decisions. |
| 4 | [`04_llm_shadow.py`](./04_llm_shadow.py) | Phase 1 (LLM_SHADOW). Rule still decides; LLM runs in parallel, predictions captured for later statistical analysis. Uses a stub LLM — swap to `OpenAIAdapter` / `AnthropicAdapter` / `OllamaAdapter` for production. |
| 5 | [`05_output_safety.py`](./05_output_safety.py) | Wrapping *LLM output* classification with `safety_critical=True`. PII / confidential markers detected before delivery to users. |
| 6 | [`06_ml_primary.py`](./06_ml_primary.py) | The end-state: `Phase.ML_PRIMARY` with a healthy ML head deciding, the rule sitting silently as the circuit-breaker target. Part 2 simulates an ML failure to show breaker trip + operator reset. |
| 7 | [`07_llm_as_teacher.py`](./07_llm_as_teacher.py) | Cold-start pattern: start at `Phase.LLM_PRIMARY` with zero labeled data, let the LLM decide + label production traffic, then train a local ML head on the accumulated labels and graduate to `Phase.ML_WITH_FALLBACK`. |

## What's not here yet

The following examples are on the roadmap (see
`docs/working/roadmap-2026-04-20.md` §1.5) but not yet written:

- Circuit-breaker-under-ML-failure demo.
- End-to-end transition curve on an ATIS-like public benchmark.
- `dendra roi` report from an accumulated outcome log.
- Integration with LangSmith / Weights & Biases telemetry.

Contributions welcome — see [`../CONTRIBUTING.md`](../CONTRIBUTING.md).

## License

Examples are licensed under **Apache 2.0** (matching the client
SDK they exercise). The `SPDX-License-Identifier: Apache-2.0`
header at the top of each file declares this explicitly. You
can copy these files into your own projects without attribution
beyond the Apache 2.0 license terms.
