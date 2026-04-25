# Security Policy

## Supported versions

Security patches are backported to the current minor release only.
Dendra follows semver: patches (1.0.x) land on 1.0 as long as 1.0
is current; minor-release cutoffs are announced in
[CHANGELOG.md](CHANGELOG.md).

| Version | Security patches |
|---|---|
| 1.0.x | ✅ yes |
| 0.2.x | ❌ no (pre-public-launch internal release; superseded) |
| 0.1.x | ❌ no (superseded) |

## Reporting a vulnerability

**Please do NOT open a public GitHub issue for security reports.**

Email the maintainer directly: `ben@b-treeventures.com`.

Include:

- A clear description of the vulnerability.
- Steps to reproduce, ideally with a minimal code sample.
- The affected Dendra version (`pip show dendra` output).
- If relevant: the threat model you're assuming (attacker position,
  preconditions, what they gain).

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

- The Dendra library itself (`pip install dendra`) at the current
  supported version.
- The reference CLIs (`dendra init`, `dendra analyze`, etc.).
- The shipped LLM / ML adapters.
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
- Dendra Cloud (when it exists) — separate security policy will apply.

## Cryptographic signing

Dendra releases are:

- **Git tags** — signed with the maintainer's SSH key (ed25519).
- **PyPI releases** — built and published via GitHub Actions trusted
  publisher, with provenance attestations via Sigstore.
- **Commits** — signed on `main` and all tagged releases.

Verify a release:

```bash
git verify-tag v1.0.0
```

## Hall of fame

Contributors who have responsibly disclosed security issues will be
listed here (with permission).
