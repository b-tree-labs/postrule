# Licensing

Dendra is a dual-licensed project. Different parts of this repository
ship under different licenses, picked to match how the code is used.

| Component | License | File |
|---|---|---|
| **Client SDK** — the decorator, config, storage, logger, LLM / ML adapters, telemetry, viz, and benchmarks that customers import into their own processes | **Apache License, Version 2.0** | [`LICENSE-APACHE`](./LICENSE-APACHE) |
| **Dendra-operated product surface** — the analyzer, ROI reporter, research/graduation tooling, CLI, and any hosted server components | **Business Source License 1.1** with Change Date **2030-05-01** and Change License **Apache 2.0** | [`LICENSE-BSL`](./LICENSE-BSL) |

The split is explained in developer-friendly terms in
[`LICENSING.md`](./LICENSING.md). Each source file carries the
license that governs it in its own header; when in doubt, check the
per-file header rather than guessing by directory.

## In one paragraph

You can `pip install dendra`, import the decorator into your
production code, embed Dendra in any product, redistribute it,
modify it, and ship it commercially — that's the Apache 2.0 part,
and it covers everything you'd normally expect from a library. A
narrower set of Dendra-operated components (the analyzer, the
CLI, future hosted services) ship under the Business Source
License so that another company can't take them, wrap them in a
hosted Dendra-like service, and sell it back to the market. The
Additional Use Grant in `LICENSE-BSL` explicitly allows You to
run the analyzer on Your own code, in Your own environment, in
production. On **2030-05-01**, the BSL-licensed parts
automatically convert to Apache 2.0.

## Trademarks

Neither license grants any right to use the DENDRA name or logo.
See [`trademark-strategy.md`](./docs/working/trademark-strategy.md)
for the project's position on fair use of the name.

## Commercial licensing

Commercial / enterprise licensing that removes the BSL
restrictions (e.g., for companies wishing to offer a hosted
Dendra-derivative service) is available. Contact
`licensing@b-treeventures.com`.

## Why this split

See
[`docs/working/license-strategy.md`](./docs/working/license-strategy.md)
for the full decision record, including the rationale for choosing
BSL over SSPL/ELv2, the per-module mapping, and the risks and
mitigations considered. Short version: the Apache 2.0 client SDK
preserves Dendra's primitive-positioning and citation story; the
BSL-licensed components protect a four-year moat-build window
against hyperscaler clones without compromising code
auditability or enterprise procurement acceptability.
