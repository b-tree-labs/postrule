# Postrule red-team / security test suite

This directory contains adversarial / security tests that pin the
threat model Postrule v1 ships under. Every test is decorated with
`@pytest.mark.redteam` and runs by default with the regular suite.

To run only this suite:

```
.venv/bin/python -m pytest tests/redteam/ -v
```

To opt out:

```
.venv/bin/python -m pytest tests/ -m 'not redteam'
```

## Threat model summary

Postrule is a Python library that ships:

1. A classification primitive (`LearnedSwitch`) that runs in-process.
2. A storage layer (`FileStorage`, `BoundedInMemoryStorage`) that
   appends JSON-line outcome records keyed by switch name.
3. A static-analysis lifter (`postrule.lifters.evidence`) that reads
   user source code, applies AST transforms, and emits Python modules
   into `__postrule_generated__/` directories.
4. A CLI (`postrule refresh`, `postrule init`, `postrule whoami`) that
   walks user repos and reads/writes generated files + credentials.
5. An optional cloud client (`postrule.cloud.*`) that talks to a hosted
   API for sync, registry, and team-corpus features.

### Trust boundaries

| Boundary | What flows in | What we promise |
|---|---|---|
| user input -> `dispatch` | arbitrary Python objects | rule sees value verbatim, never eval'd, never deserialized |
| `labels=` arg | strings (or `Label` instances) | accepted as opaque text or refused with clear error |
| user source -> lifter | Python source as string | parsed via `ast.parse`, never `exec`'d |
| `@evidence_via_probe(field="expr")` | string | parsed via `ast.parse(mode='eval')`, never eval'd at lift time |
| `@evidence_inputs(field=lambda: ...)` | callable | inspected as AST, never invoked at lift time |
| `__postrule_generated__/*.py` | file content | parser reads header only; never imports/exec's the file |
| `~/.postrule/credentials` | JSON file | json-parsed; non-dict / missing-key payloads refused |
| `POSTRULE_API_KEY` env var | string | opaque, never shell-interpolated |
| cloud HTTP responses | bytes/json | TLS-verified, never executed as code |

### What's IN scope

- Path traversal in `switch_name` (covered by `test_path_traversal.py`)
- Malicious labels (newlines, control chars, shell metacharacters,
  format strings, dunder names) - `test_malicious_labels.py`
- Malicious inputs (10MB string, 1000-deep nesting, prompt injection,
  pickle bombs) - `test_malicious_inputs.py`
- Generated-file RCE via crafted module - `test_generated_file_attacks.py`
- Annotation API abuse / lift-time RCE - `test_annotation_abuse.py`
- Auth bypass / credential tampering - `test_auth_abuse.py`
- Resource exhaustion (concurrency, recursion, fork, big vocabulary) -
  `test_resource_exhaustion.py`
- TLS / cert verification - `test_tls_and_cloud.py`

### What's OUT of scope (v1)

- Side-channel attacks against the ML head (timing, cache).
- Adversarial ML inputs that exploit a specific trained head's
  decision boundary. Postrule is the substrate; head robustness is
  the head's responsibility.
- Compromise of the user's local Python environment. If `pip` is
  poisoned, all bets are off.
- Cloud-server-side issues. The cloud API has its own threat model.

## Triage policy

- **Real vulnerabilities** (path traversal that escapes, code injection
  that executes, auth bypass that grants access): fixed inline,
  surgical commit, "BUG FIX:" line in test docstring.
- **Defense-in-depth gaps**: where a guard exists at one layer but
  not another. Test pins the existing guard; if a fix is non-blocking
  for v1 it goes in the v1.1 hardening table below.
- **Threat-model gaps**: documented in this README.

## Real vulnerabilities found and fixed

### 1. `postrule refresh` followed `__postrule_generated__/` symlinks outside the project root

`cmd_refresh` walked the project tree via `Path.rglob("__postrule_generated__")`
which silently follows symlinks on POSIX. A malicious
`__postrule_generated__` symlink inside the project root pointing at
e.g. `/etc` would let the walker glob (and parse-read) arbitrary `*.py`
files there. No code was executed (parse_generated_header is parse-only),
but presenting an out-of-tree path is itself information leakage.

**Severity**: low (parse-only, no exec, requires attacker write inside
the project directory).
**Fix**: `cmd_refresh` now resolves each candidate `__postrule_generated__`
dir and skips any whose `.resolve()` escapes `root.resolve()`, with a
stderr warning. Same filter applied to individual `*.py` entries.
Test: `test_refresh_walk_does_not_follow_symlink_to_outside`.

### 2. `@evidence_via_probe` strings spliced verbatim into generated source

`_extract_probe_overrides` parsed each probe string with
`ast.parse(mode='eval')` and then accepted any single-expression result.
The lifter never `eval`'d the probe at lift time (so no lift-time RCE),
but the probe AST was unparsed back into the generated source and
written to disk. A user who later ran the generated module would fire
whatever the probe expressed, so a hostile annotation like
`@evidence_via_probe(field="__import__('os').system('rm')")` was a
deferred RCE vector for any user who ran `postrule init --auto-lift` on
attacker-controlled source and then imported the result.

**Severity**: medium (deferred RCE; requires the attacker to control
function source AND the user to execute the generated module).
**Fix**: `_extract_probe_overrides` now walks the parsed expression and
raises `LiftRefused("unsafe_probe: ...")` when the probe contains a call
to any of `__import__`, `eval`, `exec`, `compile`, `open`, `getattr`,
`setattr`, `delattr` (frozen set `_FORBIDDEN_PROBE_BUILTINS`). The
probe never reaches the generated file. Tests:
`test_hostile_probe_rejected_outright`,
`test_forbidden_builtin_in_probe_rejected` (parametrized over 8
variants), `test_safe_probe_still_accepted` (regression guard).

## Defense-in-depth: existing guards that pass without code changes

| Area | Guard | Tests |
|---|---|---|
| FileStorage path traversal | `_switch_dir` rejects abs paths, `..` segments, and resolves under `base_path` | `test_path_traversal.py` (20 tests) |
| FileStorage symlink-out | `.resolve()` then `relative_to(base)` catches escape | `test_symlink_to_outside_base_refused`, `test_toctou_symlink_swap` |
| Generated-file parsing | `parse_generated_header` is regex-only, no exec | `test_parse_header_does_not_exec_file`, `test_detect_drift_does_not_exec_file` |
| Annotation lifter | `ast.parse(mode='eval')` only; never eval/exec/compile | `test_probe_string_not_executed_during_lift` (parametrized over 6 hostile probes) |
| Lambda not invoked at lift | lifter reads `kw.value.body` (AST), never calls the lambda | `test_evidence_inputs_lambda_not_called_at_lift_time` |
| Credentials loading | json.loads + isinstance checks; no eval | `test_load_credentials_does_not_eval_payload` and 3 others |
| `save_credentials` mode | post-write `os.chmod` to 0o600 (POSIX) | `test_save_credentials_enforces_0600`, `test_save_credentials_overwrites_world_readable_predecessor` |
| Cloud TLS | no `verify=False`, https-only constants, default `requests.Session` verify=True | `test_tls_and_cloud.py` (6 tests) |
| Storage record format | JSON-lines, never pickle | `test_pickle_dump_records_do_not_re_unpickle` |
| Label dispatch | dict lookup by name, never `getattr`/eval | `test_label_method_collision_no_execution`, `test_label_with_callable_does_not_eval_name` |

## Defense-in-depth gaps: v1.1 status

Five gaps were queued from the v1 round. Three landed in v1.1
(`tests/redteam/test_v1_1_did_gaps.py`); two were re-queued for v1.2
with `xfail(strict=True)` markers that will turn into XPASSes the
moment their underlying surface lands.

### Landed in v1.1

| Category | Gap | Fix |
|---|---|---|
| Header version handling | `parse_generated_header` accepted any blake2b hash length; a longer hash from a newer Postrule would otherwise raise a generic "malformed hash" error. | `refresh.py` now pins `_HASH_MIN_LEN` / `_HASH_MAX_LEN` (both 32 in v1) and emits a distinct "unsupported Postrule version" error when a hash exceeds the max, naming the version so operators upgrade Postrule rather than chasing a corrupt-file ghost. |
| `whoami` truncation | A key with embedded ANSI ESC / BEL / BS / DEL bytes rendered terminal-control chars when printed. | `cli.py` adds `_strip_unsafe_chars`; `_truncate_key` runs through it so non-printable bytes never reach stdout. The stored credential is unchanged. |

### Queued for v1.2 (xfail strict)

| Category | Gap | Reason for deferral |
|---|---|---|
| `postrule refresh` walk | `Path.rglob` follows symlinks for intermediate dir traversal. The resolved-under-root check already catches the escape case. | `follow_symlinks=False` requires Python 3.13; `requires-python` is `>=3.10`. Will swap once min Python bumps. |
| Dispatch input pickling | A custom storage backend could pickle records, turning attacker-controlled input into a deserialization sink. | Docstring landing collides with a peer change-stream rewriting `storage.py` (concurrency / lock refactor); will batch with that work. |
| Cloud SSL pinning | We rely on the system CA bundle; a compromised CA could MITM. | Needs a config surface (env var or credentials field), a cert-rotation story, and an opt-out for users behind corporate MITM proxies. v1 round pins `verify=True` + https-only via `test_tls_and_cloud.py`. |

## Sandbox dependency

The redteam suite runs under `tests/conftest.py`'s sandbox:

- `HOME` redirected to `tmp_path`.
- Outbound non-loopback sockets blocked.
- Disk writes outside `tmp_path` blocked.

Two redteam tests opt into `home_writable` (the credentials-mode
tests, where the test still writes only to `tmp_path` but needs
`Path.home()` not to be already-redirected). No other opt-outs.

## Maintaining the suite

When adding new tests:

1. Use `pytestmark = pytest.mark.redteam` at module level.
2. Pick the appropriate file by attack surface; don't sprawl.
3. If you find a real bug, fix it inline, write a `BUG FIX:` line in
   the docstring, and update the table above.
4. If you find a defense-in-depth gap, add a row to the v1.1 table
   above and a test that pins the desired post-fix behavior.
5. No `os.system` / `subprocess` to test attacks. Use Python-level
   mocks, sentinels, and the sandbox.
