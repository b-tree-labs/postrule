# Bring your own truth oracle

This is the heart of Postrule, and the thing that separates it from a
feature flag: **you decide what "correct" means, and Postrule refuses to
graduate a switch until the evidence — measured against *your* truth —
clears a statistical bar.**

No model grades its own homework. No vendor benchmark stands in for your
domain. You bring the truth; Postrule does the test.

> **The one-paragraph version.** Ship your hand-written rule as a
> `Phase.RULE` switch. Define a **truth oracle** — a callable that
> returns the ground-truth label for an input (a held-out labeled set, a
> downstream signal, a reviewer pool, or a judge committee). Hand it to
> `CandidateHarness` along with a candidate classifier. The harness runs
> both on the same inputs, scores each against your oracle, and returns a
> head-to-head **McNemar** verdict — `recommend_promote=True` only when
> the candidate is *significantly* better. You promote on your terms.

## The workflow, end to end

### 1. Seed a switch from your existing rule

You already have a rule. Decorate it. On day one it ships as `Phase.RULE`
— your code still decides, nothing has changed except that every call is
now logged.

```python
from postrule import ml_switch

@ml_switch(labels=["spam", "ham"])
def is_spam(email: str) -> str:
    # your hand-written rule — the day-one decision-maker
    return "spam" if "buy now" in email.lower() else "ham"
```

The underlying switch is reachable at `is_spam.switch` (a
`LearnedSwitch`) — that's what the harness compares against.

### 2. Bring your truth oracle

A **truth oracle** is `Callable[[input], true_label]`. It is the only
opinionated thing you have to supply, and it is where your domain
knowledge lives. The simplest one is a held-out labeled set:

```python
# A held-out validation set you trust — your ground truth.
HELD_OUT = {
    "buy now cheap watches": "spam",
    "re: lunch tomorrow?": "ham",
    "claim your prize today": "spam",
    "q3 board deck attached": "ham",
    # …enough labeled examples to power a real test
}

def truth_oracle(email: str) -> str:
    return HELD_OUT[email]
```

The oracle can be anything that returns a trustworthy label: a downstream
signal that resolves later (wrapped to wait for it), a reviewer pool's
aggregated verdict, or a high-quality language-model **judge committee**.
See [Verdict sources](verdict-sources.md) for the full catalog and the
decision matrix.

### 3. Shadow-evaluate a candidate against production

```python
from postrule import CandidateHarness

harness = CandidateHarness(
    switch=is_spam.switch,     # production baseline
    truth_oracle=truth_oracle, # your ground truth
    alpha=0.05,                # significance level (tighten to 0.01 for high-stakes)
)

# A candidate that wants to take over — an LLM-backed classifier,
# a trained head's predict, a better rule. Anything callable.
harness.register("llm-v1", llm_classifier)

# Run prod + every candidate + the oracle on the same inputs.
harness.observe_batch(HELD_OUT)
```

The harness **never modifies production**. Candidates run *alongside*,
never *instead of*. Its job is to tell you **when** a swap is justified —
not to perform it.

### 4. Read the verdict

```python
report = harness.evaluate("llm-v1")
print(report.summary_line())
# [PROMOTE] llm-v1: prod=72.0% candidate=91.0% (n=100, b=23, c=4, p=4.1e-04, alpha=0.05)
```

`recommend_promote` flips to `True` only when **both** hold:

- the two-sided **McNemar** p-value on the discordant pairs is below
  `alpha`, and
- the candidate's observed accuracy strictly exceeds production's.

That second guard is deliberate: it blocks a "statistically significant
but actually worse" promotion from a noisy, low-evidence corner. The
report also carries `p_value`, `prod_accuracy`, `candidate_accuracy`, and
the discordant-pair counts `b` (candidate right, prod wrong) and `c`
(prod right, candidate wrong) — the raw evidence the decision rests on.

### 5. Promote on your terms

```python
if report.recommend_promote:
    # Guarded by *your* deployment process — a human, a CI gate, a canary.
    is_spam.switch._rule = llm_classifier
```

Graduation is evidence-gated *and* human-gated. The statistics tell you
the swap is justified; you decide to make it.

## We won't let a model grade its own homework

When your oracle is a language-model judge (`JudgeSource` /
`JudgeCommittee`), Postrule guards against the most common way model-based
evaluation lies to you: **the classifier judging itself.** If the judge
model matches the classifier model, Postrule refuses:

```text
RuntimeError: refusing to judge with the same model as the classifier —
self-judgment biases the verdict. Use a distinct model family, a committee,
or pass allow_self_judgment=True if you explicitly accept the bias risk.
```

The bundled judge committee pairs **distinct model families** (e.g. Qwen
+ Gemma) for exactly this reason. The bias guardrail is a feature, not an
inconvenience — it's what makes the verdict trustworthy enough to gate a
production promotion on. The rationale and the opt-out are documented in
[Verdict sources → bias guardrail](verdict-sources.md).

## Why this is the thing worth paying for

A feature flag flips on your say-so. Postrule flips a classifier's
autonomy only when **your** evidence says it earned it — and keeps the
rule underneath as a safety net the whole way. The truth oracle is how
you keep ownership of "correct"; the statistical gate is how you keep the
promotion honest.

## Next

- **[`examples/19_autoresearch_loop.py`](../examples/19_autoresearch_loop.py)** —
  a complete LLM-driven autoresearch loop gated by `CandidateHarness`; a
  55%-accuracy production rule ratchets to 100% across four iterations.
- **[Verdict sources](verdict-sources.md)** — every built-in oracle, the
  bias-risk matrix, and the `VerdictSource` protocol for your own.
- **[Autoresearch loops](autoresearch.md)** — the deep dive on
  candidate generation, tournaments, and promotion callbacks.
- **[Getting started](getting-started.md)** — the full six-phase
  lifecycle if you're new.
