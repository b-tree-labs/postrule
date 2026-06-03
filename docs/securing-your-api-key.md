# Securing your Postrule API key

Your security team will ask: *"How do we store the Postrule key, and what
does Postrule expect?"* This is the canonical answer.

First, the reassurance that changes the risk calculus: **Postrule sees your
decisions, never your data.** A key authorizes writing verdicts and reading
your account — it does **not** expose your inputs, classified content,
labels, or ground truth, because those never leave your process (see the
[telemetry wire specification](legal/telemetry-shape.md)). A leaked key is
a problem to rotate, not a data breach. Protect it anyway — the guidance
below is how.

## Pick the right kind of key

Postrule keys come in two kinds, chosen on the key-creation screen:

| Kind | Use it for | Why |
| --- | --- | --- |
| **Service key** | A deployment / CI / a long-running service | Attributed to the deployment, not a person. Label one per environment so you can revoke a single deployment without disrupting others. |
| **User key** | A human's laptop, ad-hoc local use | Tied to the person who created it. Don't bake these into shared infrastructure. |

Keys look like `prul_live_<32 chars>` (production) or `prul_test_<32 chars>`
(sandbox). Use separate keys per environment.

## Where the key should live

- **CI / headless / production** → read `POSTRULE_API_KEY` from a secrets
  manager (GCP Secret Manager, AWS Secrets Manager, HashiCorp Vault,
  Doppler) or a platform secret store (GitHub Actions secrets, Cloud
  Run / Lambda secret mounts). Never a plaintext env file committed to the
  repo.
- **Local development** → use the device-flow login, not a hardcoded key:

  ```bash
  postrule login      # browser device flow; stores creds in ~/.postrule
  postrule status     # confirm who you're connected as
  ```

- **AI coding agents / MCP** → connect **keyless** via the device flow so a
  raw secret is never pasted into a tool or chat (pasting a `prul_live_…`
  key into an agent prompt is an anti-pattern):

  ```
  postrule_connect_start     # MCP tool — begins the device flow
  postrule_connect_complete  # MCP tool — polls until authorized, saves creds
  postrule_status            # MCP tool — reports connection identity
  ```

## What never to do

- **Never commit a key** to a repo (even a private one).
- **Never bake a key into a container image** — mount it at runtime from a
  secret store.
- **Never log the key** or include it in error reports / support tickets.

## Rotation and revocation

- Rotate on a regular cadence, and **immediately on any suspected
  exposure**. Issue a new key, roll it out, then revoke the old one from the
  dashboard (**Settings → API keys**).
- Keep keys **per-environment** so revoking one never takes down another.

## Detect committed keys

Add the Postrule key pattern to your secret scanning so a key can't slip
into git unnoticed:

```
prul_(live|test)_[A-Za-z0-9]{32}
```

Recommended: enable **GitHub secret scanning** / push protection, or run
[`gitleaks`](https://github.com/gitleaks/gitleaks) in CI with the rule
above.

## Roadmap (not yet shipped)

Mature security programs also expect the following; they're on the roadmap
and current status is available on request to `licensing@b-treeventures.com`:

- **Scoped / least-privilege keys** — per-project, and verdict-write vs
  read-only scopes, so a deployed key can't read billing/account data.
- **Key-usage audit** — last-used + source per key in the dashboard.
- **Enterprise readiness** — SSO/SAML on the dashboard, SOC 2.

See also: [Threat model](THREAT_MODEL.md) ·
[Telemetry wire spec](legal/telemetry-shape.md) ·
[Data Processing Addendum (template)](legal/dpa-template.md).
