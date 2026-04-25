# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0
"""Autoresearch loop driving Dendra promotions.

Run: `python examples/19_autoresearch_loop.py`

The pattern this example demonstrates is the production-substrate
play for autoresearch loops:

    Autoresearch tells you what to try.
    Dendra tells you when it worked.

The dirty secret of LLM-driven autoresearch loops today is that
they're great at *generating* candidate classifiers (new rules,
new prompts, new gating thresholds) and terrible at *deploying*
them with statistical confidence to production. Teams duct-tape
evals harnesses around their loops and call it MLOps.

Dendra is the missing piece. The :class:`CandidateHarness`
shadows every candidate against the production switch's decision,
runs paired-McNemar significance testing against a truth oracle,
and tells the loop whether each candidate is statistically
justified to promote. The rule floor of the underlying
:class:`LearnedSwitch` protects production from bad proposals
throughout.

This example fakes the autoresearch loop with a deterministic
"propose-evaluate-iterate" cycle so it runs without LLM keys.
The loop pattern itself is real — swap the propose-step for
your favorite autoresearch agent and the rest just works.
"""

from __future__ import annotations

import random
from collections.abc import Callable

from dendra import (
    CandidateHarness,
    LearnedSwitch,
    SwitchConfig,
)


# ---------------------------------------------------------------------------
# The world: ticket triage with day-zero rule and a known truth oracle
# ---------------------------------------------------------------------------
#
# Ground-truth labeling rule (what we WISH the classifier knew):
#   - "crash"/"error"/"down"/"stuck"/"broken" → bug
#   - "?" at end → question
#   - everything else → feature_request
#
# Production rule (what was hand-written six months ago):
#   - "crash" only → bug
#   - "?" at end → question
#   - everything else → feature_request
#
# The production rule misses bugs phrased as "error"/"down"/"stuck"/"broken".
# Day zero, the team didn't think of those keywords. The autoresearch loop's
# job is to discover them — and Dendra's job is to gate the discovery.

_BUG_KEYWORDS_TRUTH = ("crash", "error", "down", "stuck", "broken")


def production_rule(ticket: dict) -> str:
    """Six-month-old hand-written rule. Only catches 'crash' for bugs."""
    title = (ticket.get("title") or "").lower()
    if "crash" in title:
        return "bug"
    if title.endswith("?"):
        return "question"
    return "feature_request"


def truth_oracle(ticket: dict) -> str:
    """Ground truth — what we'd want a perfect classifier to return.

    In production, this would be a labeled validation set, a
    downstream signal that resolves later (with a wrapper that
    waits for it), a reviewer-pool aggregator, or an LLM-judge
    committee with bias guardrails.
    """
    title = (ticket.get("title") or "").lower()
    if any(kw in title for kw in _BUG_KEYWORDS_TRUTH):
        return "bug"
    if title.endswith("?"):
        return "question"
    return "feature_request"


# ---------------------------------------------------------------------------
# The autoresearch loop (faked deterministically here; in production this
# would be an LLM-driven proposal-eval-iterate cycle)
# ---------------------------------------------------------------------------


def _make_keyword_rule(keywords: tuple[str, ...]) -> Callable[[dict], str]:
    """Factory: build a rule that catches `bug` on any of these keywords."""
    def rule(ticket: dict) -> str:
        title = (ticket.get("title") or "").lower()
        if any(kw in title for kw in keywords):
            return "bug"
        if title.endswith("?"):
            return "question"
        return "feature_request"
    return rule


# Each "iteration" the loop proposes a candidate that adds keywords.
# Real loops would have an LLM read the outcome log and propose;
# we simulate by ratcheting through a known progression.
_LOOP_PROPOSALS = [
    ("crash", "error"),                                     # iter 1: add "error"
    ("crash", "error", "down"),                             # iter 2: add "down"
    ("crash", "error", "down", "stuck"),                    # iter 3: add "stuck"
    ("crash", "error", "down", "stuck", "broken"),          # iter 4: add "broken"
]


# Sample tickets the loop evaluates against. In production this would
# be live traffic; for the example we hard-code a representative mix.
_TICKETS = [
    # Bugs the production rule catches:
    {"title": "app crashes on startup"},
    {"title": "page crashes when I scroll"},
    # Bugs the production rule MISSES (no "crash" keyword):
    {"title": "got an error trying to log in"},
    {"title": "the search index is down"},
    {"title": "checkout is stuck on step 3"},
    {"title": "broken: cannot upload files"},
    {"title": "error 500 on dashboard refresh"},
    {"title": "service is down for our team"},
    # Real questions:
    {"title": "how do I export data?"},
    {"title": "is dark mode coming?"},
    # Real feature requests:
    {"title": "please add SAML support"},
    {"title": "add a CSV export option"},
    {"title": "i would love a dark theme"},
    {"title": "support for keyboard shortcuts"},
    # More bugs (mixed keywords):
    {"title": "checkout error during payment"},
    {"title": "page is broken after the update"},
    {"title": "crash report attached"},
    {"title": "everything is down right now"},
    # More features:
    {"title": "feature: bulk-upload UI"},
    {"title": "add a setting for default view"},
] * 5  # repeat to give the harness enough volume for stat power


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


def main() -> None:
    # Production switch — the real classifier in front of users.
    sw = LearnedSwitch(
        rule=production_rule,
        name="ticket_triage_prod",
        author="@examples:19",
        config=SwitchConfig(auto_record=False, auto_advance=False),
    )

    promoted: list[str] = []

    def on_promote(report) -> None:
        promoted.append(report.candidate_name)
        print(f"  >> PROMOTION RECOMMENDATION: {report.summary_line()}")

    harness = CandidateHarness(
        switch=sw,
        truth_oracle=truth_oracle,
        alpha=0.05,
        on_promote_recommendation=on_promote,
    )

    print("Production rule misses bugs phrased as 'error'/'down'/'stuck'/'broken'.")
    print("Autoresearch loop will propose keyword expansions; Dendra gates each.\n")

    # The autoresearch loop. In production this is an LLM agent
    # reading the outcome log and proposing rules. Here we ratchet
    # through deterministic proposals.
    for i, keywords in enumerate(_LOOP_PROPOSALS, start=1):
        candidate_name = f"v{i}_kw{len(keywords)}"
        candidate = _make_keyword_rule(keywords)
        harness.register(candidate_name, candidate)

        # Stream the evaluation traffic through the harness.
        # In production this is your live traffic stream.
        harness.observe_batch(_TICKETS)

        report = harness.evaluate(candidate_name)
        verdict = "PROMOTE" if report.recommend_promote else "HOLD  "
        print(
            f"iter {i}: {candidate_name:14s} kw={list(keywords)}\n"
            f"        prod_acc={report.prod_accuracy:.1%}  "
            f"cand_acc={report.candidate_accuracy:.1%}  "
            f"b={report.b}  c={report.c}  p={report.p_value:.2e}  "
            f"-> {verdict}"
        )

        # In a real loop, the agent reads the report and decides
        # what to propose next. The simulated loop just continues
        # ratcheting through its plan.

    # Final summary: which candidates cleared the McNemar bar?
    print(f"\nLoop complete. {len(promoted)} candidate(s) recommended for "
          f"promotion: {promoted}")
    print(
        "\nIn production, the autoresearch loop reads "
        "report.recommend_promote and acts on it: swap the candidate "
        "into the switch's rule, ml_head, or model;\nupdate "
        "config.starting_phase if appropriate; commit the change "
        "through your normal deployment process. The switch's rule "
        "floor stays in place\nthroughout — even a bad promotion "
        "can't remove the day-zero safety net the McNemar gate "
        "couldn't fully validate."
    )


if __name__ == "__main__":
    random.seed(0)
    main()
