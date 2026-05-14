# Data Processing Addendum (Template)

> **Template — last updated 2026-05-11.** Not legal advice. To execute
> a signed copy, email `licensing@b-treeventures.com`. Customers
> signing this template should expect minor negotiation.

This addendum (the "DPA") supplements the underlying service agreement
between the Customer (the "Controller") and B-Tree Ventures, LLC, a
Texas limited liability company doing business as B-Tree Labs (the
"Processor"), under which the Processor makes the Postrule service
available to the Controller. The DPA governs the Processor's
processing of Personal Data on the Controller's behalf for the
purposes set out in §3.

It is drafted to satisfy Article 28 of Regulation (EU) 2016/679
("GDPR") and the United Kingdom General Data Protection Regulation
("UK GDPR"). Where the underlying service agreement and this DPA
conflict on a matter of data protection, this DPA prevails.

Cross-references in this document:

- [`sub-processors.md`](sub-processors.md) — current list of
  sub-processors.
- [`access-policy.md`](access-policy.md) — Processor personnel access,
  compelled-disclosure handling, breach SLA.
- [`telemetry-shape.md`](telemetry-shape.md) — wire-format
  specification for the telemetry stream covered by §3 and §5.
- [`/privacy`](../../cloud/dashboard/app/privacy/page.tsx) — the
  rendered privacy contract presented to data subjects at sign-in.

---

## 1. Definitions

The terms below carry the meanings assigned to them in GDPR Article 4
and are reproduced here for reference; the GDPR text controls.

1.1 **"Personal Data"** — any information relating to an identified or
identifiable natural person.

1.2 **"Processing"** — any operation or set of operations performed on
Personal Data, whether or not by automated means.

1.3 **"Controller"** — the natural or legal person which determines
the purposes and means of the processing of Personal Data. Under this
DPA, the Customer.

1.4 **"Processor"** — a natural or legal person which processes
Personal Data on behalf of the Controller. Under this DPA, B-Tree
Ventures, LLC.

1.5 **"Sub-processor"** — any third party engaged by the Processor to
process Personal Data on the Controller's behalf.

1.6 **"Data Subject"** — the natural person to whom the Personal Data
relates.

1.7 **"Personal Data Breach"** — a breach of security leading to the
accidental or unlawful destruction, loss, alteration, unauthorised
disclosure of, or access to, Personal Data transmitted, stored, or
otherwise processed.

1.8 **"Telemetry Event"** — a single record produced by the Postrule SDK
at classification time and transmitted to the Processor's hosted API.
The shape of a Telemetry Event is specified in
[`telemetry-shape.md`](telemetry-shape.md) and in §5 below.

1.9 **"Service"** — the Postrule hosted API, dashboard, and associated
SDK functionality made available to the Controller under the
underlying service agreement.

Terms not defined here carry the meaning given in GDPR Article 4 or,
where the GDPR is silent, the meaning given in the underlying service
agreement.

---

## 2. Subject matter and duration

2.1 **Subject matter.** The Processor processes Personal Data limited
to the categories described in §5 ("Categories of Personal Data") for
the purposes described in §3 ("Nature and purpose of processing").

2.2 **Duration.** Processing begins when the Controller's authorised
user signs in to the Service and continues for the term of the
underlying service agreement, plus the post-termination period
described in §13.

---

## 3. Nature and purpose of processing

The Processor processes Personal Data on the Controller's behalf for
the following purposes only:

3.1 Authenticating the Controller's authorised users and binding their
account to a Service tier.

3.2 Receiving Telemetry Events from the SDK installed on the
Controller's infrastructure and persisting them to the Processor's
data store for the per-tier retention window stated in §8.

3.3 Computing aggregate cohort statistics across multiple Controllers'
Telemetry Events to produce the "tuned defaults" feature returned to
the SDK at install time.

3.4 Producing per-account usage metrics for tier enforcement, billing,
and the Service dashboard.

3.5 Operating, securing, and debugging the Service, including
short-window retention of HTTP request paths, response codes, and
error traces.

Each Telemetry Event contains the fields enumerated in §5.2; nothing
else. The Service does not process: classifier inputs, classifier
outputs, ground-truth labels, prompt text, dataset metadata,
environment variables, host fingerprints, per-call latency, per-call
cost, or stack traces. The full negative list is in
[`telemetry-shape.md` §"What is NOT sent"](telemetry-shape.md#4-what-is-not-sent).

---

## 4. Controller and Processor responsibilities

4.1 **Controller obligations.** The Controller warrants that it has
established a lawful basis under GDPR Article 6 for the processing
that this DPA covers, that it has provided the notices to Data
Subjects required by GDPR Articles 13–14, and that its instructions to
the Processor are themselves lawful.

4.2 **Processor obligations.** The Processor will process Personal
Data only on documented instructions from the Controller, including
with regard to transfers of Personal Data to a third country. The
underlying service agreement and this DPA constitute the Controller's
initial documented instructions; further instructions may be given in
writing during the term.

4.3 **No further use.** The Processor will not use the Controller's
Personal Data for any purpose other than those listed in §3. In
particular, the Processor will not sell the Controller's Personal
Data, will not use it to train models offered to third parties, and
will not combine it with other Controllers' data outside the cohort
statistics described in §3.3.

---

## 5. Categories of Personal Data and Data Subjects

5.1 **Categories of Data Subjects.** The Controller's signed-in
authorised users — typically developers, operators, or analysts
employed by or contracted to the Controller. This DPA does not cover
the Controller's own end users; those Data Subjects do not interact
with the Service.

5.2 **Categories of Personal Data received from the Controller's
infrastructure.** Per Telemetry Event:

- `switch_name` — a free-form string chosen by the Controller's
  authorised user when wrapping a classification site (e.g.
  `ticket_priority`). **The Controller chooses this string and is
  responsible for not rendering it sensitive.** The Processor has no
  technical means to detect when a switch name contains Personal Data
  and will treat the string as the Controller has chosen it.
- `phase` — one of the six lifecycle phases (`P0`–`P5`) or absent.
- `rule_correct`, `model_correct`, `ml_correct` — three independent
  booleans or absent.
- `request_id` — a per-event UUIDv4 used for idempotency on retry.

5.3 **Personal Data attached server-side.** On receipt, the hosted API
attaches:

- `account_hash` — HMAC-SHA-256 of the Controller's authorised user's
  email under a server-side pepper. Pseudonymous; the pepper is not
  exported.
- `received_at` — a server-assigned UTC timestamp.

5.4 **Personal Data collected outside the Telemetry Event stream.**

- **Account email** — collected at sign-in by the authentication
  Sub-processor (Clerk) and returned to the Processor.
- **Display name** — optional, set by the Data Subject on
  `/dashboard/settings`; defaults to absent.
- **Billing identifier** — Stripe Customer ID; the Processor never
  receives the underlying card data.
- **Operational logs** — request paths, response codes, error traces;
  retained 7 days. IP addresses are redacted from logs after 24
  hours.

5.5 **What is not received.** Classifier inputs, classifier outputs,
ground-truth labels, prompt text, dataset metadata, environment
variables, host fingerprints, per-call latency, per-call cost,
in-process model identifiers, and stack traces are not transmitted to
the Service. The SDK source is open for audit; the wire shape is
specified in [`telemetry-shape.md`](telemetry-shape.md). The
verifiable mechanism the Controller may use to confirm this is
described there.

---

## 6. Processor obligations

6.1 **Confidentiality.** The Processor ensures that personnel
authorised to process Personal Data are under an appropriate statutory
or contractual obligation of confidentiality.

6.2 **Security measures.** The Processor implements the technical and
organisational measures described in §7.

6.3 **Sub-processors.** The Processor engages Sub-processors only as
permitted by §9.

6.4 **Personal Data Breach notification.** The Processor notifies the
Controller of a Personal Data Breach as described in §10.

6.5 **Cooperation with Data Subject rights.** The Processor cooperates
with the Controller in responding to Data Subject requests as
described in §11.

6.6 **Audit cooperation.** The Processor cooperates with the
Controller's audit rights as described in §12.

6.7 **Return or deletion at end of processing.** The Processor returns
or deletes Personal Data on termination as described in §13.

---

## 7. Security measures

The Processor implements the following technical and organisational
measures. The list reflects the v1.0 implementation as of 2026-05-20;
the underlying mechanisms may change with the Service, in which case
the Processor will provide an updated description on written request.

7.1 **Transport security.** All traffic between the SDK and the hosted
API and between the dashboard and the hosted API is over TLS. The
Processor does not accept plaintext HTTP.

7.2 **At-rest encryption.** The Processor's data store is Cloudflare
D1 (a managed SQLite service). At-rest encryption is provided by the
hosting Sub-processor under its own controls; the Processor does not
hold the encryption keys.

7.3 **API key handling.** API keys issued to the Controller's
authorised users are 190-bit random strings. The Processor stores only
the HMAC-SHA-256 of each key under a server-side pepper; the plaintext
key is shown to the Data Subject once at issuance and is not
recoverable. The plaintext is not retained server-side.

7.4 **Service-token comparison.** Internal service tokens used between
the Processor's components are compared in constant time to prevent
timing-side-channel disclosure.

7.5 **Wire-shape minimisation.** The SDK is designed so that
classifier inputs, classifier outputs, and labels never enter the
network send path. The wire shape is enforced in code at
`src/postrule/cloud/verdict_telemetry.py::_build_payload`, which
constructs the payload from a fixed allow-list of fields. The
verifiable mechanism is documented in
[`telemetry-shape.md`](telemetry-shape.md).

7.6 **Tier-bounded retention.** Telemetry Events are retained for the
window stated in §8.

7.7 **Access logging.** Production database access by Processor
personnel is logged via the hosting Sub-processor's audit log. See
[`access-policy.md`](access-policy.md) for the access governance
posture.

7.8 **What the Processor does not yet have.** The Processor does not
currently hold a SOC 2 attestation or ISO 27001 certification. A SOC 2
Type 1 audit is targeted for Q4 2026; an ISO 27001 timeline has not
been set. The Processor will report progress in the annual
transparency report described in
[`access-policy.md`](access-policy.md).

---

## 8. Retention

8.1 **Telemetry Events.** Retained for the window associated with the
Controller's active tier:

| Tier     | Retention window |
|----------|------------------|
| Free     | 7 days           |
| Pro      | 30 days          |
| Scale    | 90 days          |
| Business | 1 year           |

After the retention window, Telemetry Events are hard-deleted from
the Processor's primary data store.

8.2 **Cohort contributions.** Aggregate contributions to the cohort
statistics described in §3.3 may be retained beyond the §8.1 window
**without `account_hash` linkage**. The Processor cannot reconstruct
which Controller contributed which event after this de-linkage.

8.3 **Account data.** Retained until the Data Subject's account is
deleted by the Controller or by the Data Subject.

8.4 **Operational logs.** Retained 7 days; IP addresses are redacted
after 24 hours.

8.5 **Billing records.** Retained 7 years to satisfy United States
federal tax-record retention requirements.

---

## 9. Sub-processors

9.1 **General authorisation.** The Controller authorises the Processor
to engage the Sub-processors listed in
[`sub-processors.md`](sub-processors.md) at the date this DPA is
executed.

9.2 **Notice of changes.** The Processor will give the Controller at
least **30 days' prior written notice** of any addition to or change
in the Sub-processor list. Notice is delivered by updating
[`sub-processors.md`](sub-processors.md) in the public repository and,
for Controllers who have signed a copy of this DPA, by email to the
billing contact on file.

9.3 **Right to object.** During the 30-day notice period the
Controller may object to a proposed Sub-processor in writing. The
Processor will work in good faith to address the objection; if the
Processor cannot accommodate it, the Controller may terminate the
underlying service agreement without penalty for the affected
services, on written notice given before the change takes effect.

9.4 **Flow-down.** The Processor binds each Sub-processor to data
protection obligations no less protective than those set out in this
DPA, in writing.

---

## 10. Personal Data Breach notification

10.1 **Timing.** The Processor notifies the Controller of a Personal
Data Breach affecting the Controller's Personal Data **without undue
delay and in any event within 72 hours** of the Processor becoming
aware of it. The notification mechanism is the security contact email
the Controller provides at signing; in the absence of a designated
contact, notification is delivered to the billing contact on file.

10.2 **Content of notification.** The notification describes, to the
extent then known:

- the nature of the Personal Data Breach, including where possible
  the categories and approximate number of Data Subjects and records
  concerned;
- the likely consequences of the Personal Data Breach;
- the measures taken or proposed to address the Personal Data Breach
  and to mitigate its possible adverse effects.

10.3 **Updating the notification.** Where the information required
under §10.2 cannot be provided at the time of the initial
notification, the Processor provides it in stages without further
undue delay as it becomes available.

10.4 **Notification to Data Subjects.** Notification to affected Data
Subjects under GDPR Article 34 is the Controller's responsibility; the
Processor will provide reasonable cooperation. The Processor will not
notify the Controller's Data Subjects directly on its own initiative.

---

## 11. Data Subject rights

11.1 **Cooperation.** The Processor will, taking into account the
nature of the processing, assist the Controller by appropriate
technical and organisational measures, insofar as this is possible,
for the fulfilment of the Controller's obligation to respond to
requests under GDPR Articles 15–22.

11.2 **Routing of requests.** Requests received by the Processor
directly from a Data Subject will, where the Data Subject identifies a
Controller, be forwarded to that Controller within five business days
and not actioned by the Processor on its own initiative.

11.3 **Response time.** The Processor will respond to a Controller's
written request for cooperation under §11.1 within 30 days.

11.4 **Current technical route.** The Processor's response to a
Controller's cooperation request is currently a manual workflow
initiated by email to `privacy@postrule.ai`. The Processor commits to
publishing a documented procedure for export and deletion in the
Controller-facing dashboard by **2026-Q4** and to upgrading the manual
workflow to a self-service route by **2027-Q2**. Until those features
ship, the 30-day commitment in §11.3 is honoured by manual workflow.

11.5 **Cost.** Cooperation under §11.1 is provided at no additional
charge, except where a Controller's requests are manifestly unfounded
or excessive, in which case the Processor may charge a reasonable fee
or refuse to act, in accordance with GDPR Article 12(5).

---

## 12. Audit rights

12.1 **Information on request.** On the Controller's reasonable
written request, the Processor will make available the information
necessary to demonstrate compliance with this DPA, including the
technical and organisational measures described in §7.

12.2 **Audit cadence.** The Controller may exercise an on-site or
documentary audit of the Processor's compliance with this DPA **once
per calendar year on 30 days' prior written notice**, subject to the
Processor's reasonable security and confidentiality requirements. The
Controller pays its own costs and reimburses the Processor's
reasonable costs incurred in supporting the audit.

12.3 **Third-party attestation substitution.** Where the Processor
holds a current third-party attestation (e.g. SOC 2 Type 1 or Type 2,
ISO 27001) covering the scope of the requested audit, the Processor
may satisfy §12.2 by providing the attestation report under NDA. **As
of 2026-05-20 the Processor holds no such attestation**; a SOC 2 Type
1 audit is targeted for Q4 2026.

12.4 **Investigations.** §12.2 does not limit any audit or inspection
right vested in a supervisory authority under applicable law.

---

## 13. End-of-processing

13.1 **Choice.** On termination or expiry of the underlying service
agreement, the Controller may elect, by written notice given within 30
days of termination, that the Processor either (a) return the
Controller's Personal Data, or (b) delete it.

13.2 **Default.** Absent an election under §13.1, the Processor
deletes the Controller's Personal Data within 30 days of termination.

13.3 **What deletion entails.** Deletion under §13.1(b) or §13.2 means
hard deletion of the Controller's Telemetry Events, account record,
display name, and authentication identifiers from the Processor's
primary data store within 30 days. The Processor's standard backup
rotation may retain residual copies for up to a further 35 days, after
which the backups roll off. **One narrow exception:** the
de-linked-from-`account_hash` cohort contributions described in §8.2
are retained on the basis that they no longer constitute Personal
Data; the Controller cannot recall its contribution after de-linkage.

13.4 **Billing records.** Billing records are retained as stated in
§8.5; the Processor cannot delete them without breaching United States
federal tax-record retention requirements.

13.5 **Certification on request.** On the Controller's written
request, the Processor will provide written confirmation that
deletion under §13.1 or §13.2 has been completed.

---

## 14. International transfers

14.1 **Where Personal Data is processed.** The Processor's primary
infrastructure is the Cloudflare global edge network and the
Sub-processors listed in [`sub-processors.md`](sub-processors.md). As
a consequence, Personal Data may transit and rest outside the
Controller's jurisdiction, including in the United States.

14.2 **Standard Contractual Clauses.** For transfers of Personal Data
from the European Economic Area, the United Kingdom, or Switzerland to
the United States or another country not subject to an adequacy
decision, **the Module 2 (Controller-to-Processor) Standard
Contractual Clauses** approved by the European Commission in Decision
(EU) 2021/914 of 4 June 2021 are **incorporated into this DPA by
reference**. The Processor will execute the SCCs as a stand-alone
annex on the Controller's written request.

14.3 **UK addendum.** For transfers from the United Kingdom, the
International Data Transfer Addendum to the EU SCCs issued by the UK
Information Commissioner's Office (Version B1.0, in force 21 March
2022) is incorporated by reference and will be executed alongside the
SCCs.

14.4 **Transfer impact.** The Processor will reasonably cooperate with
the Controller's transfer impact assessment by providing information
about its Sub-processors, the categories of Personal Data transferred,
and the technical and organisational measures applied to the
transferred Personal Data.

---

## 15. Governing law

15.1 **Choice of law.** This DPA is governed by the laws of the State
of Texas, without regard to conflict-of-laws principles.

15.2 **Negotiable.** A Controller required by its own jurisdiction to
specify a different governing law may negotiate the choice of law in a
signed copy of this DPA. The Processor will entertain reasonable
requests.

15.3 **Jurisdiction.** Subject to §15.2, the state and federal courts
located in Travis County, Texas have exclusive jurisdiction over
disputes arising under this DPA.

---

## 16. Order of precedence

To the extent a conflict arises among the documents governing the
relationship between the Controller and the Processor, the order of
precedence is: (1) an executed copy of the Standard Contractual
Clauses where applicable; (2) this DPA; (3) the underlying service
agreement.

---

## 17. Term and survival

17.1 **Term.** This DPA takes effect on the date of execution and
continues for the term of the underlying service agreement.

17.2 **Survival.** §10, §11, §13, §14, §15, and §17 survive
termination to the extent necessary to give them effect.

---

## Execution

For execution under signed terms, email
`licensing@b-treeventures.com`. The Processor will return a
counter-signed PDF, an executed copy of the Module 2 SCCs under §14.2,
and, where applicable, the UK addendum under §14.3.

---

*Template — not legal advice. Read by a lawyer admitted in the
Controller's jurisdiction before relying on it. Last updated
2026-05-11.*
