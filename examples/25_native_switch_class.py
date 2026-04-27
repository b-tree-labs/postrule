"""Native Switch class authoring — the v1 idiomatic way to write a Dendra
classifier from scratch.

The convention:

- ``_evidence_<name>(self, *args) -> <type>`` methods produce one typed
  field on the auto-generated evidence dataclass. Their return-type
  annotation IS the schema the LLM/ML head will see.
- ``_rule(self, evidence) -> str`` (one big rule) OR per-label
  ``_when_<label>(self, evidence) -> bool`` predicates (declaration
  order = evaluation order, first True wins).
- ``_on_<label>(self, *args)`` action handlers, auto-bound by name.
- A nested ``class Meta:`` may declare ``default_label`` (used when no
  ``_when_*`` matches) and ``no_action`` (labels the rule returns but
  for which no handler should fire).

This file walks through a realistic ticket-routing example, then
shows the same site authored two ways: with one big ``_rule``, and
with per-label ``_when_*`` predicates. Both compile down to the same
inner ``LearnedSwitch``.

Run:
    python examples/21_native_switch_class.py
"""

from __future__ import annotations

from dataclasses import dataclass

from dendra import Phase, Switch


# ---------------------------------------------------------------------------
# Domain: an internal helpdesk router.
#
# The "side effects" are stub functions standing in for what a real
# system would do (file a Jira ticket, page on-call, send a Slack
# message). They print to stdout here so the example is self-contained.
# ---------------------------------------------------------------------------


@dataclass
class Ticket:
    title: str
    body: str
    reporter: str


def file_engineering_ticket(ticket: Ticket) -> str:
    print(f"  [eng] filed engineering ticket for: {ticket.title!r}")
    return f"ENG-{hash(ticket.title) & 0xFFFF:04X}"


def file_product_ticket(ticket: Ticket) -> str:
    print(f"  [prod] filed product ticket for: {ticket.title!r}")
    return f"PROD-{hash(ticket.title) & 0xFFFF:04X}"


def page_oncall(ticket: Ticket) -> str:
    print(f"  [page] paged on-call for SEV-1: {ticket.title!r}")
    return f"PAGE-{hash(ticket.title) & 0xFFFF:04X}"


# ---------------------------------------------------------------------------
# Style 1: one big _rule method. Best when the decision is easiest to
# read as a single nested if/elif chain on multiple evidence fields.
# ---------------------------------------------------------------------------


class TicketRouter(Switch):
    """Route a ticket to engineering, product, or on-call."""

    def _evidence_severity(self, ticket: Ticket) -> str:
        """One of 'sev1', 'sev2', 'sev3' inferred from the title text."""
        text = ticket.title.lower()
        if "outage" in text or "down" in text:
            return "sev1"
        if "broken" in text or "error" in text:
            return "sev2"
        return "sev3"

    def _evidence_category(self, ticket: Ticket) -> str:
        """One of 'bug', 'feature', 'question'."""
        text = (ticket.title + " " + ticket.body).lower()
        if "feature" in text or "request" in text:
            return "feature"
        if "how do i" in text or "?" in ticket.title:
            return "question"
        return "bug"

    def _rule(self, evidence) -> str:
        if evidence.severity == "sev1":
            return "page_oncall"
        if evidence.category == "feature":
            return "product"
        return "engineering"

    def _on_page_oncall(self, ticket: Ticket):
        return page_oncall(ticket)

    def _on_product(self, ticket: Ticket):
        return file_product_ticket(ticket)

    def _on_engineering(self, ticket: Ticket):
        return file_engineering_ticket(ticket)


# ---------------------------------------------------------------------------
# Style 2: per-label _when_<label> predicates. Best when each label has
# its own isolated condition, or when adding a new label should mean
# adding one method without touching the others.
# ---------------------------------------------------------------------------


class TicketRouterWhenStyle(Switch):
    """Same logic as TicketRouter, expressed as per-label predicates."""

    def _evidence_severity(self, ticket: Ticket) -> str:
        text = ticket.title.lower()
        if "outage" in text or "down" in text:
            return "sev1"
        if "broken" in text or "error" in text:
            return "sev2"
        return "sev3"

    def _evidence_category(self, ticket: Ticket) -> str:
        text = (ticket.title + " " + ticket.body).lower()
        if "feature" in text or "request" in text:
            return "feature"
        if "how do i" in text or "?" in ticket.title:
            return "question"
        return "bug"

    def _when_page_oncall(self, evidence) -> bool:
        return evidence.severity == "sev1"

    def _when_product(self, evidence) -> bool:
        return evidence.category == "feature"

    class Meta:
        default_label = "engineering"

    def _on_page_oncall(self, ticket: Ticket):
        return page_oncall(ticket)

    def _on_product(self, ticket: Ticket):
        return file_product_ticket(ticket)

    def _on_engineering(self, ticket: Ticket):
        return file_engineering_ticket(ticket)


# ---------------------------------------------------------------------------
# Demo: feed a few tickets through both routers; verify they agree.
# ---------------------------------------------------------------------------


def main() -> None:
    samples = [
        Ticket(
            title="Production database is down",
            body="all users seeing 500s",
            reporter="@alice",
        ),
        Ticket(
            title="Feature request: dark mode",
            body="please add a setting",
            reporter="@bob",
        ),
        Ticket(
            title="Broken login button on mobile",
            body="iOS Safari shows the spinner forever",
            reporter="@carol",
        ),
    ]

    router_a = TicketRouter()
    router_b = TicketRouterWhenStyle()

    print(f"router_a.name        = {router_a.name!r}")
    print(f"router_a.phase()     = {router_a.phase()}")
    assert router_a.phase() is Phase.RULE
    print()

    for ticket in samples:
        print(f"Routing: {ticket.title!r}")
        result_a = router_a.dispatch(ticket)
        result_b = router_b.dispatch(ticket)
        print(f"  Style 1 ({type(router_a).__name__}): label={result_a.label!r}  action_result={result_a.action_result!r}")
        print(f"  Style 2 ({type(router_b).__name__}): label={result_b.label!r}  action_result={result_b.action_result!r}")
        assert result_a.label == result_b.label, (
            f"the two styles disagreed on {ticket.title!r}: "
            f"{result_a.label!r} vs {result_b.label!r}"
        )
        print()

    print("Both authoring styles agree on every sample.")


if __name__ == "__main__":
    main()
