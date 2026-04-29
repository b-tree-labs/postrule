# Contributing to Dendra

Thanks for considering a contribution. Dendra is
[split-licensed](./LICENSE.md) — Apache 2.0 on the client SDK,
BSL 1.1 on Dendra-operated components — and welcomes community
pull requests, issues, and discussion.

## Quick start for contributors

```bash
git clone https://github.com/b-tree-labs/dendra.git
cd dendra
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev,train,bench,viz]'
pytest tests/
```

## Philosophy

Dendra is a **primitive**. The core design goal is a small, coherent
API surface that composes cleanly — not a sprawling toolkit. Before
sending a PR that adds to the public surface, please open an issue to
discuss whether the addition belongs in core or in a companion
library.

What definitely belongs in core:

- Phase semantics, routing, the six-phase vocabulary.
- Statistical gate mechanisms.
- Storage backends implementing the `Storage` protocol.
- LLM / ML adapter protocol implementations for major providers.
- Measurement-and-reproducibility tooling (research runner, viz).
- Security properties (circuit breaker, safety-critical cap, shadow
  isolation).

What probably belongs in a companion repo:

- Domain packs (pre-trained ML heads for specific verticals).
- Enterprise-only features (federation, SOC 2 artifact generation,
  hosted dashboards, billing integration).
- IDE plugins, LSP servers, editor integrations.

## Pull request checklist

- [ ] Tests for new behavior (we aim for ≥90% on new code).
- [ ] `ruff check src/ tests/` passes cleanly.
- [ ] `pytest tests/` passes on Python 3.10–3.13.
- [ ] Commits are signed (`git commit -S`) **and** include a
  DCO sign-off (`git commit -s`) — see "Developer Certificate of
  Origin" below.
- [ ] New files carry a license header matching their target
  bucket (Apache 2.0 for client-SDK modules, BSL for
  Dendra-operated modules — see `LICENSE.md` for the mapping).
- [ ] CHANGELOG entry in the `[Unreleased]` section.
- [ ] For API changes: update `src/dendra/__init__.py` `__all__`,
  add a docstring, and note if it's a breaking change.
- [ ] For patent-relevant architectural changes: open an issue
  first. Dendra's architecture is covered by a filed provisional
  patent, and contributions that materially change the claimed
  method may require additional discussion about IP.

## Commit conventions

- **Commit style:** imperative tense. `feat(scope): add X`, not
  "added X".
- **Signed commits required** on `main`. Set up SSH signing per
  [GitHub's instructions](https://docs.github.com/en/authentication/managing-commit-signature-verification).
- **Conventional-commit prefixes** encouraged: `feat`, `fix`,
  `docs`, `chore`, `test`, `refactor`, `perf`.

## Code style

Dendra uses **ruff** for lint and format. Configuration is in
`pyproject.toml`. The pre-commit config runs ruff + detect-secrets
automatically; install the hooks with:

```bash
pip install pre-commit
pre-commit install
```

Guidelines that don't fit in ruff rules:

- **Zero marketing in docstrings.** Say what the code does, not why
  it's awesome.
- **Comments explain why, not what.** Named functions + short
  expressions document themselves.
- **Type hints on all public API.** Private helpers optional.
- **Protocols over abstract base classes** for extension points.
- **No new runtime dependencies** without discussion. Optional
  extras are the norm (see `pyproject.toml`'s
  `[project.optional-dependencies]`).

## Testing

- Unit tests live in `tests/` — one file per source module
  (`test_core.py`, `test_storage.py`, …).
- Benchmarks (`tests/test_latency.py`, `tests/test_security_benchmarks.py`)
  are marked with `pytest.mark.benchmark` — opt-in via `pytest -m
  benchmark`.
- **Every new behavior path gets a test.** See
  `tests/test_output_safety.py` as a reference for how to structure
  scenario-driven tests.
- Integration tests that hit networks (benchmark loaders, LLM
  adapters) are gated behind optional extras — no test in the
  default suite requires network access.

## Reproducibility

Everything in `docs/papers/2026-when-should-a-rule-learn/` is
reproducible via `scripts/reproduce.sh`. If you change anything that
affects the paper's measured numbers, regenerate and commit the
updated results.

## Reporting issues

- **Bugs:** open a GitHub issue with a minimal reproducer + your
  Python version + Dendra version (`pip show dendra`).
- **Security:** see [SECURITY.md](SECURITY.md). Don't file security
  issues publicly.
- **Feature requests:** issue first; PR after discussion.

## Code of conduct

By participating you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Developer Certificate of Origin (DCO)

Dendra uses the [Developer Certificate of Origin
1.1](https://developercertificate.org/) for contributions. Every
commit must carry a `Signed-off-by:` line. Add it automatically
with `git commit -s`; verify with `git log`.

The sign-off is your attestation of the full text at
`https://developercertificate.org/`. In short: you wrote the
change, or you have the right to submit it under the project's
licenses, and you understand the contribution is public.

PRs missing sign-off will be blocked by CI. For prior commits,
rebase and append the sign-off (`git rebase -i --signoff`).

## License on contributions

By contributing, you agree that your contribution will be
licensed under the same license as the file it modifies:

- Contributions to files under **Apache 2.0** (client SDK
  modules) are licensed under Apache 2.0.
- Contributions to files under **Business Source License 1.1**
  (Dendra-operated components) are licensed under BSL 1.1 with
  the same Change Date and Change License parameters.
- New files must declare one of the two licenses in their
  header. See existing files in `src/dendra/` for the canonical
  header blocks.

If you're unsure which license applies to a new file, open an
issue or ask in your PR — maintainers will confirm before merge.

You affirm that you have the right to make the grant above
(either as the copyright holder, or as someone authorized by
the copyright holder) via the DCO sign-off.

## Trademarks

DENDRA and B-TREE LABS are trademarks (or pending trademarks) of
B-Tree Ventures, LLC. Contributions may reference these names
descriptively (e.g., "improves the Dendra CLI's error handling")
but may not use them in a way that implies endorsement or
affiliation without explicit permission. See
[`TRADEMARKS.md`](./TRADEMARKS.md).

---

_Copyright (c) 2026 B-Tree Ventures, LLC (dba B-Tree Labs).
Split-licensed — see `LICENSE.md`._
