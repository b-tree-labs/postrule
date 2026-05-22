# Licensing

Postrule is a dual-licensed project. Different parts of this repository
ship under different licenses, picked to match how the code is used.

| Component | Subtree | License | File |
|---|---|---|---|
| **Client SDK** — decorator, config, storage, logger, LLM / ML adapters, telemetry, viz, and benchmarks that customers import into their own processes | `src/postrule/` (most files) | **Apache License, Version 2.0** | [`LICENSE-APACHE`](./LICENSE-APACHE) |
| **Postrule-operated Python surface** — analyzer, ROI reporter, research / graduation tooling, CLI, cloud-side helpers | `src/postrule/{analyzer,auth,cli,cloud/*,lifters/*,mcp_server,research,roi}.py` (per-file allowlist in `.github/workflows/license-check.yml`) | **Business Source License 1.1** with Change Date **2030-05-01** and Change License **Apache 2.0** | [`LICENSE-BSL`](./LICENSE-BSL) |
| **Cloudflare Worker code** — api, collector, dashboard, aggregator, security-ops | `cloud/{api,collector,dashboard,aggregator,security-ops}/**` | **BSL 1.1** (Change Date 2030-05-01) | [`LICENSE-BSL`](./LICENSE-BSL) |
| **VS Code extension** — IDE integration for `postrule init` / suggestions | `cloud/vscode-postrule/**` | **BSL 1.1** (Change Date 2030-05-01) — provisional; may relicense to Apache if positioned as "core client integration" | [`LICENSE-BSL`](./LICENSE-BSL) |
| **Marketing site** — landing page, copy, styles, scripts | `landing/**` (except `landing/assets/*` and `landing/wasm/postrule_analyzer.py`) | **BSL 1.1** (Change Date 2030-05-01) | [`LICENSE-BSL`](./LICENSE-BSL) |
| **Landing-shipped WASM analyzer** — Python source built to WASM for in-browser analysis | `landing/wasm/postrule_analyzer.py` | **BSL 1.1** | [`LICENSE-BSL`](./LICENSE-BSL) |
| **Brand assets** — logos, marks, social previews | `landing/assets/*.svg`, `brand/**` | **BSL 1.1** for source format + **Trademark** governance | [`LICENSE-BSL`](./LICENSE-BSL) + [`TRADEMARKS.md`](./TRADEMARKS.md) |
| **JSON config + data** — pricing tiers, LLM prices, analyzer outputs, cohort priors, package metadata | `landing/data/*.json`, `landing/insights/*.json`, `cloud/*/package.json` etc. | **BSL 1.1** (declared via per-directory `LICENSE` files since JSON has no comment syntax for SPDX) | [`LICENSE-BSL`](./LICENSE-BSL) + each dir's `LICENSE` |

The split is explained in developer-friendly terms in
[`LICENSING.md`](./LICENSING.md). Each source file in the in-scope
extensions (`.py`, `.ts`, `.tsx`, `.js`, `.mjs`, `.cjs`, `.css`, `.html`)
carries the license that governs it in its own header. JSON / TOML / lock
files cannot carry inline SPDX headers; for those, see the directory-level
`LICENSE` file in the same directory. When in doubt, check the per-file
header rather than guessing by directory.

The boundary is enforced by `.github/workflows/license-check.yml` on every
PR. Adding a new BSL-licensed file requires editing the `is_bsl_allowed`
function in that workflow (intentional friction) and mirroring the change
in this document.

## In one paragraph

You can `pip install postrule`, import the decorator into your
production code, embed Postrule in any product, redistribute it,
modify it, and ship it commercially — that's the Apache 2.0 part,
and it covers everything you'd normally expect from a library. A
narrower set of Postrule-operated components (the analyzer, the
CLI, future hosted services) ship under the Business Source
License so that another company can't take them, wrap them in a
hosted Postrule-like service, and sell it back to the market. The
Additional Use Grant in `LICENSE-BSL` explicitly allows You to
run the analyzer on Your own code, in Your own environment, in
production. On **2030-05-01**, the BSL-licensed parts
automatically convert to Apache 2.0.

## Trademarks

Neither license grants any right to use the POSTRULE name or logo.
See [`TRADEMARKS.md`](./TRADEMARKS.md) for the project's
position on fair use of the name.

## Commercial licensing

Commercial / enterprise licensing that removes the BSL
restrictions (e.g., for companies wishing to offer a hosted
Postrule-derivative service) is available. Contact
`licensing@b-treeventures.com`.

## Why this split

The Apache 2.0 client SDK preserves Postrule's primitive-
positioning and citation story; the BSL-licensed components
protect a four-year moat-build window against hyperscaler
clones without compromising code auditability or enterprise
procurement acceptability. On the BSL Change Date
(**2030-05-01**), all BSL-licensed files automatically convert
to Apache 2.0.
