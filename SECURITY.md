# Security Policy

## Supported versions

Security patches are backported to the current minor release only.
Postrule follows semver: patches (1.0.x) land on 1.0 as long as 1.0
is current; minor-release cutoffs are announced in
[CHANGELOG.md](CHANGELOG.md).

| Version | Security patches |
|---|---|
| 1.0.x | ✅ yes |
| 0.2.x | ❌ no (pre-public-launch internal release; superseded) |
| 0.1.x | ❌ no (superseded) |

## Reporting a vulnerability

**Please do NOT open a public GitHub issue for security reports.**

Email security findings to `security@postrule.ai`.

Include:

- A clear description of the vulnerability.
- Steps to reproduce, ideally with a minimal code sample.
- The affected Postrule version (`pip show postrule` output).
- If relevant: the threat model you're assuming (attacker position,
  preconditions, what they gain). Postrule's documented threat model
  is in [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md).

You can expect:

- **Acknowledgement within 72 hours** (usually much faster).
- **Triage decision within 5 business days** — accepted, rejected,
  or request for clarification.
- **Patch timeline** depending on severity:
  - Critical (RCE, auth bypass): target 7-day patch + coordinated
    disclosure.
  - High (data exfiltration, integrity breach): target 14-day patch.
  - Medium / Low: next scheduled release.
- **Credit** in the CHANGELOG release notes if you want it. Anonymous
  reports are also welcome.

## Scope

**In scope:**

- The Postrule library itself (`pip install postrule`) at the current
  supported version.
- The reference CLIs (`postrule init`, `postrule analyze`, `postrule login`,
  etc.).
- The shipped LLM / ML adapters.
- **Postrule Cloud surface** — the api Worker at `api.postrule.ai` and
  the dashboard at `app.postrule.ai`. Includes the bearer-auth `/v1/*`
  endpoints, the service-token `/admin/*` endpoints, the telemetry
  intake at `POST /v1/verdicts`, and the Clerk-authed dashboard
  surfaces (switches list, per-switch report cards, insights
  enrollment, settings, billing).
- Documented behavior that affects classification correctness,
  decision-path isolation, circuit-breaker persistence, or the
  safety-critical cap.

**Out of scope:**

- Vulnerabilities in transitive dependencies — report those to the
  upstream project directly (we'll bump dependency pins once a patch
  is available).
- Misuse of the library that ignores documented guidance (e.g.,
  disabling `safety_critical=True` on an authorization classifier).
- LLM provider-side issues (OpenAI, Anthropic, Ollama, etc.) — report
  to the provider.
- Sub-processor-side issues (Cloudflare, Clerk, Stripe, GitHub) —
  report to the provider; we'll coordinate on impact assessment.
  The published sub-processor list is at
  [`docs/legal/sub-processors.md`](docs/legal/sub-processors.md).

For the cloud surface specifically, four documents in `docs/legal/`
expand on what we collect, who can access it, and how we'll respond
to incidents:

- **[Data Processing Addendum (template)](docs/legal/dpa-template.md)** —
  GDPR Article 28 template, Module 2 SCCs by reference for EU
  customers; execute via `licensing@b-treeventures.com`.
- **[Sub-processor list](docs/legal/sub-processors.md)** — third
  parties that touch customer telemetry, with 30-day prior notice
  on additions.
- **[Access + disclosure policy](docs/legal/access-policy.md)** —
  personnel-access principles, 24-hour compelled-disclosure
  notification (unless legally prohibited), 72-hour breach SLA,
  and the explicit catalogue of unmade claims.
- **[Telemetry wire spec](docs/legal/telemetry-shape.md)** — exact
  on-the-wire format of every event the SDK sends, with a
  programmatic verification recipe.

## Supply-chain posture

We maintain a defense-in-depth posture against supply-chain
attacks: CI workflows are scoped to limit fork-PR impact, the
publish pipeline uses OIDC trusted-publishing with provenance
attestations, and dependency alerts are monitored continuously
with high-severity findings patched same-week.

For procurement-grade detail, see the legal docs under
`docs/legal/` (DPA template, published sub-processor list, access
+ disclosure policy, telemetry wire spec).

## Cryptographic signing

Postrule releases are:

- **Git tags** — signed with the maintainer's SSH key (ed25519).
- **PyPI releases** — built and published via GitHub Actions trusted
  publisher, with provenance attestations via Sigstore.
- **Commits** — signed on `main` and all tagged releases.

Verify a release:

```bash
git verify-tag v1.1.0
```

## Hall of fame

Contributors who have responsibly disclosed security issues will be
listed here (with permission).
