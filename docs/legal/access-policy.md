# Access policy, compelled disclosure, and breach SLA

> **Last updated 2026-05-11.** Companion to
> [`dpa-template.md`](dpa-template.md) §6–§10 and the rendered
> [Privacy Policy](../../cloud/dashboard/app/privacy/page.tsx).

This page describes who at B-Tree Ventures, LLC (dba B-Tree Labs)
sees customer data, on what terms, under what oversight, and what
happens when an outside party — including a government — asks for it.

The document states what is in place today. Where a control is
aspirational rather than implemented, it is named as such, with a
date.

---

## 1. Who has access today

B-Tree Labs is a small team. At the time of writing, the engineering
function is one person (the founder, Benjamin Booth). All production
data — including customer Telemetry Events, account records, and
billing identifiers — is technically accessible to engineering
personnel for support, debugging, and product development.

This is the most honest statement of the access posture in 2026. As
the team grows, the same role-based access controls and audit logging
described below will gate every additional person, and this page will
be updated to reflect the new headcount, the access matrix, and the
review cadence.

What this means in practice:

- A customer support request that requires data inspection is handled
  by the same person who wrote the code path under inspection.
- Production database access goes through Cloudflare's authenticated
  console, behind Cloudflare Access with multi-factor authentication,
  and is logged in Cloudflare's audit log.
- Engineering personnel are bound by the confidentiality terms of
  their employment or contractor agreement, including a duty not to
  disclose customer Personal Data outside the scope of their role.

What this does **not** mean:

- The Processor does not access customer Personal Data for any
  purpose outside those listed in [`dpa-template.md`](dpa-template.md)
  §3 — in particular, not to train models offered to third parties
  and not to combine with other customers' data outside the
  de-linked cohort statistics described there.

---

## 2. Access principles

The following principles apply today and will continue to apply as
the team grows:

- **Least privilege.** Production credentials are issued for the
  narrowest scope sufficient to do the job. Read-only credentials
  are preferred for support and debugging; write-capable credentials
  are reserved for incident response and scheduled maintenance.
- **Need to know.** Access to a specific customer's Personal Data is
  taken only when a specific request or specific incident makes that
  access necessary. Browsing is not an authorised use.
- **Logged.** Every authenticated access to the production database
  is recorded in the hosting Sub-processor's audit log. Logs are
  retained for the audit-log retention window the Sub-processor
  applies (Cloudflare retains audit logs for one year on the Workers
  Paid plan).
- **Reviewed.** Production access rights are reviewed **once per
  quarter** against the current set of authorised personnel.
  Quarterly review reports are retained internally for the duration
  of the company.

---

## 3. Personnel security

3.1 **Background checks.** Not currently performed. Will be
implemented before the second production-capable engineer joins the
company, and applied retroactively to existing personnel at that
point.

3.2 **Training.** Every engineering hire with production access will
complete security training within **30 days of access being granted**.
Training covers: handling Personal Data, recognising phishing,
secrets hygiene, breach response, and the contents of this page and
[`dpa-template.md`](dpa-template.md). Training is reviewed and
re-taken annually.

3.3 **Offboarding.** On termination, all production credentials and
SSO sessions are revoked within one business day. Hardware tokens
are returned within five business days. The offboarding checklist is
maintained alongside the deployment runbook.

3.4 **Today's reality.** With a single-person engineering function,
§3.1–§3.3 are operational items the founder pre-commits to before
expanding the team. They are written here so a customer signing a
DPA today can hold the company to them later.

---

## 4. Compelled disclosure (government and legal requests)

4.1 **Commitment to notify.** If the Processor receives a lawful
request from a government agency, law-enforcement body, or party to
civil litigation for a specific customer's Personal Data, the
Processor will **notify the affected customer within 24 hours of
receipt of the request**, by email to the security contact on file,
**unless legally prohibited from doing so** (for example, a National
Security Letter or an order under 18 U.S.C. § 2705(b) prohibiting
notice).

4.2 **Challenge.** The Processor will challenge requests that appear
overbroad, that lack a clear lawful basis, or that demand data
outside the categories the Processor actually holds, where the
challenge has a reasonable basis in law. The Processor's threshold
for challenge is whether a reasonable person reviewing the request
would conclude that the legal basis is doubtful; the threshold is
not "challenge everything," because frivolous challenges burn customer
goodwill and the Processor's credibility.

4.3 **Scope of data the Processor can produce.** The Processor can
produce, in response to a lawful request:

- Account records (email, display name, Clerk user_id, billing
  identifier, account tier).
- Telemetry Events for the customer within the per-tier retention
  window stated in [`dpa-template.md`](dpa-template.md) §8.
- Operational logs within the 7-day window stated in
  [`dpa-template.md`](dpa-template.md) §8.4. Per
  [`telemetry-shape.md`](telemetry-shape.md), IP addresses are
  redacted from logs after 24 hours and are not stored against
  Telemetry Events at all; a request for "IP addresses associated
  with this account over the past month" therefore cannot be
  satisfied beyond the last 24 hours of operational logs.
- The Processor cannot produce classifier inputs, classifier
  outputs, labels, or prompt content for any customer: the Processor
  does not receive them.

4.4 **What the Processor will not do.** The Processor will not
expand its data collection in response to a request — for example,
will not start logging IP addresses against Telemetry Events to
satisfy a request that the current logging window does not cover. A
request for data the Processor does not hold is answered with a
description of what the Processor does and does not hold.

4.5 **Transparency reporting.** See §6.

---

## 5. Breach notification

5.1 **Window.** For any incident affecting the security,
confidentiality, integrity, or availability of customer Personal
Data, the Processor will notify the affected customer **within 72
hours of becoming aware of the incident**. The window aligns with
GDPR Article 33(1).

5.2 **Channel.** Notification is sent by email to the security
contact on file, and, where appropriate, by a notice posted to the
Service status page.

5.3 **Content.** The notification describes, to the extent known at
the time:

- the nature of the incident;
- the categories and approximate number of Data Subjects and records
  concerned;
- the likely consequences;
- the mitigations taken or proposed.

Where information is not yet available at first notification, it is
provided in stages as the investigation progresses. The Processor
does not delay first notification while waiting for a complete
picture.

5.4 **Notification of Data Subjects.** GDPR Article 34 places the
duty to notify affected Data Subjects on the controller (the
customer). The Processor will not notify the customer's Data
Subjects on the customer's behalf or on its own initiative. The
Processor will provide such cooperation as the customer reasonably
requests to support the customer's notification.

5.5 **Post-incident report.** After material incidents, the
Processor produces a post-incident report and shares it with the
affected customer within 30 days of incident closure. The report
covers root cause, timeline, mitigations, and follow-on actions.

5.6 **What is not a breach.** Routine drops at the SDK's rate
limiter, queue-overflow drops, single-call request failures, and
similar normal-operation losses of telemetry are not breaches. Loss
of Telemetry Events to opt-out toggling is by design.

---

## 6. Transparency reporting

6.1 **2026.** During the first calendar year of operation, the
Processor will report **each government or law-enforcement request
individually** at <https://dendra.run/transparency> (or a successor
URL on this repository) within 30 days of receipt, subject to the
legal-prohibition exception in §4.1.

6.2 **2027 onward.** From the close of calendar year 2026 onward,
the Processor will publish an **annual transparency report** covering
the prior calendar year, including:

- the aggregate count of government and law-enforcement requests
  received, by jurisdiction;
- the count of requests complied with in full, in part, and not at
  all;
- the count of requests where notice to the affected customer was
  delayed or prohibited;
- the count of accounts affected.

6.3 **First annual report.** Targeted publication: **2027-Q1**,
covering 2026.

---

## 7. Vulnerability disclosure

7.1 **Channel.** Security findings are reported by email to
`security@b-treeventures.com`. The full process is documented in
[`SECURITY.md`](../../SECURITY.md).

7.2 **Acknowledgement.** Acknowledgement of receipt within 72 hours,
usually faster.

7.3 **Triage decision** within five business days: accepted,
rejected, or request for clarification.

7.4 **Patch timeline:**

- Critical (RCE, authentication bypass, data exfiltration in
  progress): targeted 7-day patch with coordinated disclosure.
- High (data exfiltration, integrity breach): targeted 14-day patch.
- Medium / Low: next scheduled release.

7.5 **Credit.** Reporters are credited in the CHANGELOG release
notes by default; anonymous reports are honoured on request.

---

## 8. Posture statements that are intentionally not made

This section catalogues things the Processor does **not** claim,
because they are not true today. They are listed so a procurement
reviewer can confirm what is missing without needing to ask.

- **SOC 2 Type 1 or Type 2.** Not held. Targeted: Type 1 audit Q4
  2026.
- **ISO 27001.** Not held. No public timeline.
- **HIPAA Business Associate readiness.** Not held. The Processor
  does not currently sign Business Associate Agreements; healthcare
  customers should treat the Service as out of HIPAA scope.
- **PCI DSS.** The Processor is out of PCI DSS scope because card
  data never enters its infrastructure (Stripe Checkout handles it
  end-to-end). The Processor does not claim PCI DSS compliance for
  any other reason.
- **24/7 incident response.** Not staffed. The Processor commits to
  the 72-hour breach notification in §5 and to best-effort
  same-business-day response on security email; it does not commit
  to a follow-the-sun on-call rotation.
- **Penetration testing on a fixed cadence.** Not in place.
  Pen-test scope and cadence will be defined as part of the SOC 2
  Type 1 readiness work in 2026.

The list is updated when an item moves from "not held" to "held."

---

## 9. Contact

- Security incidents and vulnerability reports:
  `security@b-treeventures.com`. PGP key published at
  <https://dendra.run/.well-known/security.txt> (in progress —
  targeted 2026-Q3).
- Compliance documents and DPA execution:
  `licensing@b-treeventures.com`.
- Privacy / Data Subject rights: `privacy@dendra.run`.

---

*Last updated 2026-05-11. Not legal advice. Read by a lawyer
admitted in the relevant jurisdiction before relying on it.*
