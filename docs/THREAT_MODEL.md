# Dendra threat model

This document describes Dendra's trust boundaries and the threats considered
in its design, with mitigations linked to the specific code that implements
them. It exists for enterprise pre-checks and for the security-conscious
adopter who wants to know **what Dendra defends against by design**, **what
is left to the operator**, and **what is honestly out of scope**.

The document is conservative: where a mitigation is implemented in code,
it cites the file. Where a mitigation is planned for a later release,
it is marked `[v1.1]` or `[v1.x]` so readers can distinguish what ships
today from what is on the roadmap.

Last reviewed: 2026-05-04 against `src/dendra/` at HEAD.

## What ships today

Dendra v1.0 is **a Python library and CLI** plus a small hosted account
surface for account creation, API keys, and billing:

- **Client SDK** (Apache 2.0) — `import dendra` into your own process.
  Decorator, switch, gates, storage, adapters, verdict sources, autoresearch
  primitives, async API.
- **In-process analyzer + ROI + research tooling** (BSL 1.1) — the
  `dendra` CLI: `dendra analyze`, `dendra init`, `dendra roi`,
  `dendra bench`, `dendra plot`, `dendra quickstart`. **Runs locally
  in the user's process; not a network service.**
- **Hosted account surface** — a Cloudflare-hosted dashboard
  (`app.dendra.run`) and Worker (`api.dendra.run`) for account creation
  (Clerk auth), API key issuance, and billing (Stripe). These exchange
  metadata only; **no source code, prompt content, or labels are
  transmitted** by the v1.0 client to any Dendra-operated service.

There is **no Dendra-hosted analyzer service** in v1.0 — the Analyzer
runs only in the user's own process. Sections of this document that
describe "SDK ↔ remote Analyzer" or "Hosted-analyzer ↔ tenant"
boundaries are forward-looking and marked `[v1.1]`.

## Trust boundaries

### 1. SDK ↔ in-process Analyzer

**Status today:** the Analyzer (`src/dendra/analyzer.py`) is a
local-only static AST scanner that runs in the user's own Python
process via `dendra analyze`. There is **no network protocol, no
authentication boundary, and no remote service** between the SDK
and the Analyzer at v1.0 — they share the same process and the
same trust domain.

**Future-state (Hosted Analyzer, `[v1.1]`):** if a Dendra-hosted
analyzer is introduced, the boundary becomes:

- **Protocol** `[v1.1]` — HTTPS over a REST API. Existing
  `api.dendra.run` Worker is the natural host.
- **Authentication** `[v1.1]` — API key in a request header (the
  `dndr_live_…` keys already issued by the v1.0 dashboard apply
  unchanged); OAuth/SSO integrations after.
- **Transport security** `[v1.1]` — TLS 1.3 floor; Cloudflare-managed
  certificates per the existing `*.dendra.run` zone.

### 2. Switch ↔ outcome-log data store

The `LearnedSwitch` writes outcome records to a `Storage` backend.
Five backends ship today:

| Backend | Locality | Concurrency | Durability |
|---|---|---|---|
| `BoundedInMemoryStorage` (default) | in-process | single-thread | none (process-local) |
| `InMemoryStorage` | in-process | single-thread | none |
| `FileStorage` | local disk | POSIX `flock` (single host) | append-log; `fsync=True` opt-in for host-crash durability |
| `SqliteStorage` | local disk | WAL-mode (1 writer + N readers) | ACID within the DB |
| `ResilientStorage` | wraps any primary | inherits | adds in-memory fallback + drain-on-recovery |

A `PostgresStorage` exists on the v0.3+ roadmap; not yet shipped.

**Encryption at rest:** **not implemented out of the box.**
`FileStorage` and `SqliteStorage` accept a `redact=` callable that
runs on every append, before the record reaches the in-memory queue
or the disk — adopters use it to scrub PII / regulated content
(`docs/storage-backends.md` §"Redaction hook"). The
`_format_line` / `_parse_line` overrides on `FileStorage` are an
explicit hook for adopters to plug in encryption, compression, or
custom serialization. Dendra ships **none of this enabled by default**;
encryption-at-rest is the operator's responsibility today.

### 3. Verifier model — local vs remote

`JudgeSource` (and `JudgeCommittee`) wraps a `ModelClassifier`. Four
shipped adapter families:

| Adapter | Network egress |
|---|---|
| `OllamaAdapter` / `OllamaAsyncAdapter` | local (default `http://localhost:11434`) — no third-party egress |
| `LlamafileAdapter` / `LlamafileAsyncAdapter` | local (default `http://localhost:8080/v1`) — no third-party egress |
| `dendra.bundled` (llama-cpp-python) | local — GGUF is lazy-downloaded once from the Dendra CDN; subsequent calls are local-only |
| `OpenAIAdapter` / `OpenAIAsyncAdapter` | **remote — to OpenAI** |
| `AnthropicAdapter` / `AnthropicAsyncAdapter` | **remote — to Anthropic** |

**For the remote case, the classification input + the candidate label
are sent to the third-party LLM provider.** Compliance-sensitive
deployments should prefer a local adapter or a custom
`VerdictSource` that routes through an internal model gateway. The
`require_distinct_from=` guardrail and the verdict-source contract
are agnostic to this choice.

### 4. Hosted ↔ tenant isolation

The v1.0 hosted account surface (`api.dendra.run` Worker +
`app.dendra.run` dashboard) handles account creation, API key
issuance, and billing. It exchanges metadata only — **no source
code, prompt content, or labels are transmitted by the v1.0 client
to any Dendra-operated service.**

Tenant isolation today is logical: each row in the `users`,
`api_keys`, and `subscriptions` tables in the production D1 database
carries a `user_id` foreign key, and every server-side route enforces
the row-level filter from the authenticated session. There is no
multi-tenant analyzer execution to isolate at v1.0 because the
analyzer runs in the user's own process.

**Hosted Analyzer (`[v1.1]`):** if a Dendra-hosted analyzer is
introduced, this section will be revised to document the per-tenant
isolation strategy (logical / dedicated schema / dedicated instance)
and the data-residency contract.

## Threats considered and mitigations

| Threat | Likelihood (today) | Mitigation |
|---|---|---|
| Compromised verifier model returns adversarial verdicts | Medium | Distinct-from guardrail: `JudgeSource(require_distinct_from=...)` rejects construction at switch-build-time when classifier and judge resolve to the same `(adapter_class, model_string)` pair (`src/dendra/verdicts.py::_same_model`). Multi-judge consensus via `JudgeCommittee` (majority / unanimous / confidence-weighted modes) bounds single-judge influence. |
| ML head silent regression in `Phase.ML_PRIMARY` | High | Persistent circuit breaker — `LearnedSwitch._save_breaker_state` / `_load_breaker_state` write the tripped state to disk so it survives process restart. While tripped, classification falls back to the rule. Reset is operator-driven via `reset_circuit_breaker(operator=...)` and emits a telemetry event. |
| Removal of the rule safety floor | Low | Architectural — the rule is always present in `_classify_impl`'s fallback paths. `safety_critical=True` *additionally* caps `phase_limit` at `ML_WITH_FALLBACK` and refuses construction at `ML_PRIMARY` (`src/dendra/core.py::__post_init__` + `LearnedSwitch.__init__`), so authorization-class deployments cannot accidentally graduate past the rule. |
| Concurrent writers corrupt the outcome log | Medium | `FileStorage(lock=True)` (default) uses POSIX `flock` for serialization across processes + threads on a single host. `SqliteStorage` uses WAL mode. **NFS / SMB are not safe** — flock is unreliable across network filesystems; use a local disk or `SqliteStorage`. **Windows has no POSIX flock** — `FileStorage` falls back to a no-op lock and emits a one-time `UserWarning`; use `SqliteStorage` for cross-platform multi-process safety. |
| Process crash mid-write loses outcomes | Medium | `FileStorage(fsync=True)` opts into per-write fsync (host-crash-durable, slower). Default `fsync=False` is kernel-buffer-durable (process-death-safe, host-crash window of typically a few seconds). `SqliteStorage(sync="NORMAL")` is the default; `sync="FULL"` for stricter durability. |
| Storage backend outage takes the classifier offline | Medium | `ResilientStorage` wraps any primary backend with an in-memory fallback that drains on recovery. `degraded_writes` and `degraded_evictions` counters surface the fallback's activity so operators can see when the audit chain has been partially captured in memory only. |
| Audit-chain tampering | Medium | **Append-only by convention** — `Storage.append_record` is the only documented mutation API; there is no public delete. JSONL files are line-append-only on disk; SqliteStorage uses an INSERT-only schema. **NOT cryptographically signed.** A privileged attacker with disk access can rewrite past records; this is **out of scope for v1.0**. Cryptographic tamper-evidence (hash-chain / signed records) is on the `[v1.x]` roadmap if compliance buyers require it. |
| PII / regulated content leaked via outcome log | Medium | `redact=` hook on `FileStorage` and `SqliteStorage` runs before the record enters the in-memory queue or the disk; raw input never touches storage when correctly wired. The hook is intentional seam, not default — operators must opt in (`docs/storage-backends.md` §"Redaction hook"). |
| Self-judgment bias inflates accuracy in the gate | Medium | `JudgeSource(guard_against_same_llm=True)` (default) refuses construction if `judge_model` and `require_distinct_from` resolve to the same model. The same check fires from `LearnedSwitch.__init__` when both a `model=` and a `verifier=` are wired. Cited literature: G-Eval (NAACL 2023), MT-Bench (NeurIPS 2023), Chatbot Arena (ICML 2024). |
| Statistical-gate noise yields false promotion | Medium | `McNemarGate(alpha=0.01, min_paired=200)` is the default — paired-McNemar with a 1% false-promotion-rate ceiling (5× the conventional 5%, accounting for family-wise testing across phases). `min_paired=200` floor refuses promotion until 200 paired correct-outcome records exist. `safety_critical=True` further caps the maximum reachable phase. |
| Token / API-key exfiltration | Medium | Standard secret hygiene only — Dendra reads `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` from the environment and never persists them. Operator-side: rotate keys on a defined cadence, store in a secret manager rather than `.env` files in git, and prefer per-environment short-lived keys where the provider supports them. |
| Adversarial input to a `MODEL_SHADOW` LLM | Low (rule decides; LLM is observation-only) | The rule remains the sole decision-maker in `Phase.MODEL_SHADOW`; the LLM's output is only logged. Jailbreak / prompt-injection corpus tested in `tests/test_security_benchmarks.py` (20 patterns from publicly-documented AgentDojo / InjecAgent / OWASP LLM Top-10 families). The rule floor holds across the corpus by design. |
| Adversarial-shadow latency starves classification | Low | `_classify_impl`'s shadow-LLM path absorbs adapter exceptions and returns control to the rule's decision. A hung shadow LLM does not block the user-visible classification (covered by `tests/test_security_benchmarks.py::TestAdversarialLatency`). |

## Out of scope

- **Side-channel attacks on the host running the SDK.** Timing
  analysis of classify latency to infer rule branches, cache-line
  attacks against the model, etc. — Dendra inherits the host's
  threat model and adds no defenses here.
- **Compromise of the underlying LLM provider's infrastructure.**
  If OpenAI / Anthropic / your hosted Ollama instance is breached,
  the adversary can return arbitrary verdicts. Mitigations:
  multi-provider committees (`JudgeCommittee`), bounded blast radius
  via the rule floor + circuit breaker.
- **Physical security of self-hosted deployments.** Dendra trusts
  the host's filesystem, process boundary, and memory. An attacker
  with root on the host can rewrite the audit chain (see
  "Audit-chain tampering" above).
- **Cryptographic tamper-evidence on the audit chain (v1.0).** The
  log is append-only by convention, not by signature. See the
  `[v1.x]` marker in the audit-chain row above for the roadmap.
- **DOS / abuse rate-limiting at the SDK layer.** Dendra is a
  library; the operator's request-throttling layer (queue, API
  gateway, web framework) is the right place for rate limits.

## Audit-chain integrity claims

The outcome log is the basis for compliance attestations (HIPAA
audit trails, regulated-decision provenance, ROI claims). Dendra's
v1.0 integrity claims are explicit and conservative:

- **Append order is preserved.** `Storage.load_records` returns
  records in append order — the protocol contract.
- **No delete API ships.** Backends do not expose record deletion;
  age-out is by FIFO eviction (`BoundedInMemoryStorage`) or segment
  rotation (`FileStorage`), never targeted deletion.
- **Multi-process safety on a single host** via POSIX `flock`
  (`FileStorage`) or WAL mode (`SqliteStorage`).
- **Host-crash durability** is opt-in (`fsync=True` on
  `FileStorage`, `sync="FULL"` on `SqliteStorage`); the default is
  process-death-safe, not host-crash-safe, with a typical few-
  second window of at-risk tail writes.
- **Provenance per record.** Each record carries `author` (the
  switch's provenance string) and `source` (`"rule"`, `"model"`,
  `"ml"`, `"judge:<model>"`, `"committee:<ids>(<mode>)"`,
  `"callable:<name>"`, `"webhook:..."`, `"reviewer:..."`) so audit
  filters can separate machine verdicts from human-reviewed truth.

What v1.0 **does not claim**:

- The audit chain is **not cryptographically signed**. A privileged
  attacker with disk access can rewrite past records without leaving
  a tamper indicator. If your compliance regime requires
  tamper-evidence, layer Dendra on top of an append-only store with
  cryptographic guarantees (e.g., AWS QLDB, an immutable object-
  store bucket with object-lock, a signed-log service) via a custom
  `Storage` implementation. Dendra's `Storage` protocol is
  intentionally minimal so this composition is straightforward.

The full `Storage` protocol contract is in `src/dendra/storage.py`
docstrings; the redaction-hook recipe + custom-backend example are
in `docs/storage-backends.md`.

## Reporting a vulnerability

Per [`SECURITY.md`](../SECURITY.md): privately email
**`ben@b-treeventures.com`** — do not open a public GitHub issue for
security-sensitive findings. Acknowledgement within 72 hours; triage
decision within 5 business days; patch timeline tiered by severity
(7-day target for critical, 14-day for high, next scheduled release
for medium / low). Credit in the CHANGELOG release notes is offered
on request.
