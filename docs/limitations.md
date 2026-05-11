# Dendra, limitations

What works at v1.0, what's deferred, and what's permanently out of scope.

Status tags appear inline next to each item:

- **Permanent**: structural; will not change.
- **v1.1**: planned; not in v1.0.
- **v1.2**: planned, after v1.1.
- **Considering**: under design review, may or may not ship.

## 1. Function shape requirements

- **Single positional input (today), multi-arg via auto-pack** [v1]. The classifier function takes one positional argument at the v1.0 baseline. Multi-arg signatures are supported in v1 via auto-packing: the decorator inspects `inspect.signature(...)` and synthesizes a packed input. `*args` and `**kwargs` work via dict packing; defaults are preserved; type hints are required for the LLM/ML head schema.
- **Returns a label name (string), not a computed value** [Permanent]. The classifier returns one of a known finite set of label strings. Functions that compute and return a structured value (a dataclass, a tuple, a domain object) are not classifiers in Dendra's sense.
- **Pure or pure-after-side-effect-extraction** [v1]. The classifier body must be free of side effects, or its side effects must be extractable into label-keyed action handlers (`_on_<label>` methods, `Label(on=...)`, dict-labels). Auto-lift (Phases 2-3) extracts side effects mechanically; until then, the user does the extraction.

## 2. What can't be a classification site

- **Tests, fixtures, setup code** [Permanent]. pytest test functions, fixtures, and module-level setup are not classification sites. They have no labels and no production routing decision.
- **Output is a structured value, not a label** [Permanent]. If the function returns a tuple, dict, dataclass, or computed scalar (a price, a score, a parsed object), it is not a classifier. Wrap a classifier around the inputs that decide which strategy produces the value.
- **Decision needs out-of-process state we can't see** [Permanent]. If the rule consults a remote service, a database, or any state outside the Python process and that state is essential to the decision, the LLM/ML head cannot be given the same evidence. Either expose the state as evidence (auto-lift, `@evidence_inputs`) or refuse.
- **Decision is order-dependent across calls (state machine)** [Permanent]. If `f(x)` at time T depends on what `f(...)` returned at T-1, the function is a state machine, not a classifier. The lifecycle's paired-correctness math assumes per-input independence.

## 3. Hidden state, how to make it visible

The auto-lift mechanism makes implicit dependencies explicit. v1 ships with full auto-lift across the categories below.

- **Globals** (`FEATURE_FLAGS["x"]`, module-level config): auto-liftable [v1]. The lifter generates a `_gather` that reads the global at dispatch time and packs the value into the evidence dataclass.
- **`self.attr`** (instance state on a method): auto-liftable [v1]. Same mechanism; `self.<name>` reads land in `_gather`.
- **Mid-function I/O** (`db.lookup(text)` consulted only for evidence): auto-liftable [v1]. Read happens in `_gather`; the classifier sees the result, not the call.
- **Closures over mutable outer state**: dispatch-time snapshot [v1]. `_gather` reads the closure variable per-dispatch; for `typing.Final` / frozen types the snapshot is taken once at decoration.
- **Dynamic dispatch** (`getattr(self, name)`, attribute lookup with a runtime-computed key): explicit annotation only [Permanent]. The user declares each evidence field via `@evidence_inputs(field=lambda ...)`. Auto-detection cannot statically reason about dynamic attribute access.
- **`eval` / `exec` / metaprogramming**: refused [Permanent]. The lifter declines with a specific diagnostic. Some code cannot be statically reasoned about; saying so is the right behavior.

## 4. Exception semantics

- **Action exceptions are captured by default** (`action_raised`). A handler that raises is captured on `result.action_raised` rather than propagated. The classification decision survives handler bugs. This is v1.0 behavior, retained at v1.
- **`propagate_action_exceptions=True` restores parity** [v1]. Set on `@ml_switch(...)` or `SwitchConfig(propagate_action_exceptions=True)`. Action handlers re-raise after recording. Trade-off: a misbehaving handler now propagates to the caller, but classification telemetry still records `action_raised` for postmortem.
- **Evidence exceptions propagate by default** (auto-lift). When `_gather` raises (the original mid-function read raised), the exception propagates to the caller exactly as the un-lifted function would have raised. No silent swallowing.

## 5. Performance

- **Cold-path overhead per dispatch**: roughly 1.00 µs p50 at Phase.RULE (`dispatch`); 0.96 µs p50 (`classify`); 1.50 µs p50 at Phase.ML_PRIMARY with the model stubbed. Measured on Apple M5 / Python 3.13. Full matrix + reproduce instructions in [`docs/benchmarks/perf-baselines-2026-05-01.md`](benchmarks/perf-baselines-2026-05-01.md).
- **`_gather` adds I/O cost when evidence-lifted**. If the lifted classifier reads a database row in `_gather`, that read happens on every dispatch. The original function had the same read, so the cost is preserved, not added. For lifted code where the read is expensive, the bypass path (call the classifier directly with a pre-packed input) is the escape hatch.
- **Async overhead profile**: every sync entry point has an async peer (`aclassify`, `adispatch`, `arecord_verdict`). Async language-model adapters (`OpenAIAsyncAdapter`, `AnthropicAsyncAdapter`, `OllamaAsyncAdapter`, `LlamafileAsyncAdapter`) ship in v1. A 3-judge committee runs roughly 3x faster under async parallel evaluation; see `examples/16_async_committee.py`.
- **Per-dispatch evidence-gather benchmark** (Phase 0 sizing): pending. The auto-lift design flags this as an open question; concrete numbers land with the Phase 3 evidence lifter.

## 6. Roadmap (versioned)

**v1.0 (ships 2026-05-20).** Native `dendra.Switch` class authoring pattern (subclass with `_evidence_*` / `_rule` / `_on_*` methods); `@ml_switch` decorator (existing API, retained); multi-arg packing; full auto-lift across globals, `self`, mid-function I/O, closures (Phases 2-3 of the auto-lift design); drift detection (Phase 4); prescriptive analyzer (Phase 5); account system; `propagate_action_exceptions` config knob; MCP server; cross-phase test suite; six-phase lifecycle; head-to-head evidence gates; async API; verdict sources; CandidateHarness; Tournament.

**v1.1.** TBD based on real-user telemetry. The list of candidate items will be set after v1.0 ships and we observe which limitations users hit first.

**Considering.** Deep IDE plugins (PyCharm / VS Code rename hooks, refactor-aware drift detection); A2A integration (agent-to-agent classification primitive); runtime AST mode (decorator parses, transforms, and exec's instead of writing sibling files; rejected for v1 on debuggability grounds, may revisit).
