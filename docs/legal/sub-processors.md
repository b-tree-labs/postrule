# Sub-processors

> **Last updated 2026-05-11.** For procurement use. Companion to
> [`dpa-template.md`](dpa-template.md) §9 and the rendered
> [Privacy Policy](../../cloud/dashboard/app/privacy/page.tsx).

This page lists the third parties B-Tree Ventures, LLC (dba B-Tree
Labs, the "Processor") engages to process customer Personal Data on
the Customer's behalf in connection with the Postrule service. It is
published under GDPR Article 28(2) so that the Customer is informed of
each Sub-processor before processing begins.

## Current Sub-processors

| Sub-processor             | Service                                                       | Data accessed                                                                                          | Location           | Why                                  |
|---------------------------|---------------------------------------------------------------|--------------------------------------------------------------------------------------------------------|--------------------|--------------------------------------|
| **Cloudflare, Inc.**      | D1, Workers, KV, Pages, Access                                | All Telemetry Events, dashboard payloads, account records, all customer-facing traffic                 | Global edge        | Hosting + compute platform           |
| **Clerk, Inc.**           | Authentication (sign-in, session, OAuth identity)             | Email address + Clerk user_id                                                                          | United States      | Authentication service               |
| **Stripe, Inc.**          | Billing and payment processing                                | Email address + tier metadata; no Telemetry Events                                                     | United States      | Card processing (PCI DSS scope)      |
| **GitHub, Inc.**          | OAuth identity provider (alternate sign-in path)              | Email address + GitHub user_id                                                                         | United States      | Authentication (optional path)       |
| **Customer's chosen LLM provider** (BYOK)        | LLM judge calls when the Customer supplies an API key | Customer-supplied prompts and responses; **the Customer is the controller of this flow, not the Processor** | Provider-dependent | Bring-your-own-key LLM judging       |

---

## Entry detail

### Cloudflare, Inc.

**Legal entity.** Cloudflare, Inc., 101 Townsend Street, San
Francisco, California 94107, United States.

**Service provided.** Cloudflare D1 (managed SQLite) is the Processor's
primary data store for account records, Telemetry Events, and
usage metrics. Cloudflare Workers host the API and aggregator runtime.
Cloudflare KV holds short-lived cohort statistics and rate-limit state.
Cloudflare Pages serves the dashboard. Cloudflare Access governs
operator-side administrative routes.

**Data accessed.** All Telemetry Events transmitted by the SDK, all
dashboard server-rendered payloads, all account records, all HTTP
request logs at the edge. Cloudflare's edge sees the originating IP
address on each request; the Processor does not store IPs from
Telemetry Event traffic.

**Location.** Cloudflare operates a global edge network. Personal Data
may transit and rest in any of Cloudflare's points of presence,
including data centres in the United States and the European Economic
Area. The D1 database for the Service is provisioned in Cloudflare's
WNAM (Western North America) region.

**Contractual basis.** Cloudflare publishes a Data Processing Addendum
at <https://www.cloudflare.com/cloudflare-customer-dpa/> that the
Processor is bound to as a Cloudflare customer. The Cloudflare DPA
incorporates the Module 3 (Processor-to-Sub-processor) Standard
Contractual Clauses for transfers out of the EEA.

---

### Clerk, Inc.

**Legal entity.** Clerk, Inc., 660 King Street, San Francisco,
California 94107, United States.

**Service provided.** User sign-in (email/password and OAuth),
session-cookie issuance, and the user-profile surfaces the Processor
embeds in the dashboard. Clerk returns to the Processor only the
fields the Processor reads (email + Clerk user_id); the Processor does
not call any other Clerk endpoint at runtime.

**Data accessed.** The signed-in user's email address and Clerk
user_id. The Processor does not transmit Telemetry Events or
classifier-related data to Clerk.

**Location.** Clerk hosts its production environment in the United
States. Cross-border transfers from EEA, UK, or Swiss Data Subjects to
the United States are covered by Clerk's published Data Processing
Addendum and the Module 3 SCCs incorporated therein.

**Contractual basis.** Clerk's DPA is available at
<https://clerk.com/legal/dpa> and the Processor is bound to it as a
Clerk customer.

---

### Stripe, Inc.

**Legal entity.** Stripe, Inc., 354 Oyster Point Boulevard, South San
Francisco, California 94080, United States.

**Service provided.** Card and bank-transfer collection during paid
tier signup and renewal; subscription lifecycle (creation, prorations,
cancellations); receipts and tax-record retention. Stripe Checkout
hosts the payment form; the Processor never receives card numbers.

**Data accessed.** The Customer's billing-contact email address, the
Customer's company name where supplied, the Service tier the Customer
has selected, and the Stripe Customer ID. The Processor receives no
Telemetry Events into the Stripe Sub-processor boundary and Stripe
receives no Telemetry Events from the Processor.

**Location.** Stripe hosts its production environment in the United
States and the European Economic Area, depending on the customer's
billing jurisdiction.

**Contractual basis.** Stripe's DPA is available at
<https://stripe.com/legal/dpa> and the Processor is bound to it as a
Stripe customer.

---

### GitHub, Inc.

**Legal entity.** GitHub, Inc., 88 Colin P. Kelly Jr. Street, San
Francisco, California 94107, United States. Wholly-owned subsidiary of
Microsoft Corporation.

**Service provided.** Identity provider for the "Sign in with GitHub"
path. The Processor does not depend on GitHub for any function other
than identity assertion.

**Data accessed.** The signed-in user's email address (the verified
primary address on the GitHub account) and the GitHub user_id. The
Processor does not request additional OAuth scopes.

**Location.** United States.

**Contractual basis.** GitHub's Data Protection Agreement applies to
the Processor as a GitHub user; the published version is at
<https://docs.github.com/en/site-policy/privacy-policies/github-data-protection-agreement>.

---

### Customer's chosen LLM provider (BYOK)

**Legal entities (per Customer choice).** Anthropic, PBC; OpenAI,
L.L.C.; Google LLC; DeepInfra, Inc.; any other provider the Customer
configures.

**Service provided.** When the Customer chooses to use an LLM judge
inside the Postrule SDK and supplies their own API key for it, the SDK
calls the chosen provider directly from the Customer's infrastructure.

**Role allocation — important.** In this flow the Customer (not the
Processor) is the controller relative to the chosen LLM provider; the
LLM provider is the Customer's processor under whatever DPA the
Customer has signed with that provider. The Processor neither
intermediates the call nor stores its content. The Processor's only
involvement is supplying the SDK code that constructs and sends the
HTTP request from the Customer's machine.

**Data accessed.** Whatever the Customer's prompt to the judge
contains. The Processor does not see this content.

**Location.** Wherever the chosen provider hosts. The Customer is
responsible for ensuring that this transfer complies with the
Customer's own data protection obligations.

**Contractual basis.** None between the Processor and the chosen
provider with respect to this flow. The Customer holds the contract
with the provider.

---

## Notice and objection

The Processor will give the Customer **at least 30 days' prior written
notice** of any addition to or change in the Sub-processor list.
Notice is delivered by updating this page in the public repository
and, for Customers who have signed [`dpa-template.md`](dpa-template.md),
by email to the billing contact on file.

A Customer may object to a proposed Sub-processor in writing within
the 30-day notice period. The Processor will work in good faith to
address the objection. If the Processor cannot accommodate the
objection, the Customer may **terminate the underlying service
agreement without penalty for the affected services** by giving
written notice before the change takes effect.

---

## Out of scope

The following third parties touch the Processor's business but do not
process customer Personal Data and are not Sub-processors for this
purpose:

- GitHub, Inc. (as the host of this source repository — distinct from
  GitHub's role as identity provider above).
- The Customer's network operator, DNS resolver, and other transit
  infrastructure.
- The Processor's accounting, legal, and tax advisors, who see
  aggregate billing data but no Telemetry Events.

If any of these adds a function that brings it within Article 28's
definition of a Sub-processor, the Processor will update this page
under the §"Notice and objection" mechanism.

---

## Contact

- Sub-processor questions: `licensing@b-treeventures.com`.
- Security-related concerns about Sub-processors:
  `security@postrule.ai`.

*Last updated 2026-05-11. Not legal advice.*
