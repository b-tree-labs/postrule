# `postrule.toml`

Postrule reads an optional `postrule.toml` in the working directory at decorate time to learn what cloud project each `@ml_switch` belongs to. Committing this file separates **project binding** (which cloud project a switch reports to) from **identity** (whose credentials sign the verdicts) — the project lives in the repo, the credential lives per-environment. Any host that has credentials auto-connects to the right project with zero per-machine setup.

## Schema

```toml
# Repo-wide default. Every switch in this repo reports to "<org>/<project>"
# unless a more specific rule overrides it.
[project]
org     = "your-org"
project = "your-service"

# Per-switch overrides. Match by the switch's name (which is the wrapped
# function's __name__ unless you passed `name="..."` to @ml_switch).
# `org` is inherited from [project]; only the project portion changes.
[switches.intent]
project = "your-service"

[switches.file_intake]
project = "intake"
```

## Resolution chain

`@ml_switch` resolves the project slug at decorate time, in this order (first hit wins):

1. **Explicit kwarg** — `@ml_switch(project="acme/foo")`
2. **`postrule.toml [switches.<name>] project`** — composed with the org as `"<org>/<that>"`
3. **`postrule.toml [project] project`** — composed as `"<org>/<that>"`
4. **`git remote get-url origin`** — last two path segments, e.g. `owner/repo`
5. **`pyproject.toml [project] name`** — the project name (without org composition)
6. **literal `"default"`** — final fallback so no switch silently disappears from the dashboard

## Examples

A web service with one project per repo:

```toml
[project]
org     = "acme-co"
project = "billing-service"
```

A monorepo with multiple deployables. Each switch's project is named explicitly:

```toml
[project]
org     = "acme-co"
project = "monorepo-default"

[switches.charge_router]
project = "billing"

[switches.token_extract]
project = "auth"

[switches.intent_classifier]
project = "ml-platform"
```

A repo with no committed binding (fine; the auto-derive chain catches it):

```toml
# no postrule.toml at all → falls through to `git remote get-url origin`
# (returns "owner/repo") or `pyproject.toml [project] name`.
```

## Privacy posture

`postrule.toml` lives in your repo. Postrule sends only the resolved slug on each verdict's wire payload, never the file contents. The cohort registry strips `project`, `project_slug`, `project_id`, and `project_name` from anything shared cross-account, so even on cohort contributions the slug never leaks. See [`docs/reference/privacy.md`](privacy.md) for the full posture.

## Compatibility

| Postrule SDK | Behavior on a repo with `postrule.toml` |
|---|---|
| ≥ this release | Reads it; per-switch override wins, then repo default. |
| Pre-`postrule.toml` | Ignores the file; falls through to git remote / pyproject. |

So you can add `postrule.toml` to a shared repo without breaking older deployments.

## Related

- [`@ml_switch(project="…")`](decorator.md) — explicit override (highest priority)
- Issue [#36](https://github.com/b-tree-labs/postrule/issues/36) — original motivation: separating identity from project binding
- Issue [#107](https://github.com/b-tree-labs/postrule/issues/107) — the project grouping MVP that this file extends
