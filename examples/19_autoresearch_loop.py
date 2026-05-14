# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
"""Karpathy-style autoresearch loop, gated by ``CandidateHarness``.

Run: `python examples/19_autoresearch_loop.py`

The autoresearch loop pattern (popularised by Andrej Karpathy):

    +------------+      +------------+      +------------+
    |  PROPOSE   | ---> |  EVALUATE  | ---> |  REFLECT   | ---+
    +------------+      +------------+      +------------+    |
          ^                                                   |
          +---------------------- next iter ------------------+

A language-model agent reads recent results (REFLECT), proposes a fresh
candidate classifier (PROPOSE), the candidate runs against a
truth oracle (EVALUATE), and the loop repeats. The agent decides
when to stop.

This example wires that loop. ``CandidateHarness`` is the
EVALUATE rung — it shadows each candidate against the production
switch on the same inputs, runs a head-to-head significance
test, and returns a ``CandidateReport``. The loop's ``REFLECT``
step reads that report; the loop's ``PROPOSE`` step is the place
a language-model agent plugs in.

The propose-step here is a deterministic stand-in (a hardcoded
ratchet through known keyword expansions) so the example runs
offline with no API keys. Replace ``propose_next_candidate`` with
a language-model-backed function and the rest of the loop is unchanged.

What the harness gives your loop, in one line each:

- *Bounded false-promotion rate.* False promotions are
  capped at the ``alpha`` you pass (default ``0.05``), not
  by the candidate generator's appetite.
- *Faster convergence per paired sample.* Head-to-head
  testing on the same inputs is statistically tighter than
  independent-samples testing — 1.7–6× faster on the four
  NLU benchmarks shipped here.
- *Reproducible promotion decisions.* Every recommendation
  carries a p-value and the discordant-pair counts
  (``b``, ``c``) that drove it.

For the situations where each of these matters most — and
the situations where they don't — see ``docs/scenarios.md``.
That doc opens with an at-a-glance table of high-volume,
high-stakes industries (card fraud, AML, SOC, prior-auth,
brand safety, returns fraud, customs) where the autoresearch
loop's promotion gate carries direct financial weight.
"""

from __future__ import annotations

import random
from collections.abc import Callable

from postrule import (
    CandidateHarness,
    CandidateReport,
    LearnedSwitch,
)

# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


def main() -> None:
    sw = LearnedSwitch(rule=production_rule)
    harness = CandidateHarness(switch=sw, truth_oracle=truth_oracle, alpha=0.05)

    print("Production rule misses bugs phrased as 'error'/'down'/'stuck'/'broken'.")
    print("Loop will propose keyword expansions; harness gates each.\n")

    history: list[CandidateReport] = []
    for iter_num in range(1, MAX_ITERS + 1):
        # 1. PROPOSE — the agent reads history and produces a new candidate.
        #    Swap this for a model call: read history, decide what to try next.
        proposal = propose_next_candidate(history)
        if proposal is None:
            print("Agent says: nothing left to try. Stopping.")
            break
        name, candidate = proposal

        # 2. EVALUATE — harness shadows the candidate, head-to-head vs production.
        harness.register(name, candidate)
        harness.observe_batch(_TICKETS)
        report = harness.evaluate(name)

        # 3. REFLECT — the agent reads the report. Stop on convergence or
        #    diminishing returns; otherwise feed it into the next propose.
        history.append(report)
        _print_report(iter_num, report)
        if should_stop(report):
            print("Agent says: candidate accuracy at ceiling. Stopping.")
            break

    promoted = [r.candidate_name for r in history if r.recommend_promote]
    print(f"\nLoop complete after {len(history)} iteration(s).")
    print(f"Recommended for promotion: {promoted}")
    print(
        "\nIn production, the loop reads ``report.recommend_promote`` "
        "and acts on it: swap the candidate into the switch's rule, "
        "ml_head, or model and ship through your normal deployment "
        "process. The rule floor stays in place throughout — even a "
        "bad promotion can't remove the day-zero safety net."
    )


# ---------------------------------------------------------------------------
# The three loop steps (PROPOSE / EVALUATE / REFLECT)
# ---------------------------------------------------------------------------
#
# EVALUATE is implemented by ``CandidateHarness.evaluate`` directly;
# the other two are below. The PROPOSE step is the language-model agent hook.


# Deterministic stand-in for the language model proposer. Real loops use an
# agent that reads ``history`` (the prior CandidateReports + the
# ground-truth misses each surface) and proposes the next refinement.
_PROGRESSION = [
    ("v1_kw2", ("crash", "error")),
    ("v2_kw3", ("crash", "error", "down")),
    ("v3_kw4", ("crash", "error", "down", "stuck")),
    ("v4_kw5", ("crash", "error", "down", "stuck", "broken")),
]


def propose_next_candidate(
    history: list[CandidateReport],
) -> tuple[str, Callable[[dict], str]] | None:
    """PROPOSE step. Returns ``(name, candidate_classifier)`` or None to stop.

    Production swap-in: a language-model agent that reads ``history`` (the
    prior reports), inspects which inputs the production rule got
    wrong, and emits the next refinement to try.
    """
    idx = len(history)
    if idx >= len(_PROGRESSION):
        return None
    name, keywords = _PROGRESSION[idx]
    return name, _make_keyword_rule(keywords)


def should_stop(report: CandidateReport) -> bool:
    """REFLECT step's stop-decision. Real loops layer in plateau detection,
    cost ceilings, or agent-controlled halt. Here: stop on perfect
    accuracy."""
    return report.candidate_accuracy >= 1.0


# ---------------------------------------------------------------------------
# World setup — production rule, truth oracle, evaluation traffic.
# Below the loop because the loop is the point of the file.
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
# The production rule misses bugs phrased as "error"/"down"/
# "stuck"/"broken". Day zero, the team didn't think of those
# keywords. The autoresearch loop's job is to discover them — and
# the harness's job is to gate the discovery.

_BUG_KEYWORDS_TRUTH = ("crash", "error", "down", "stuck", "broken")

# Stop after this many propose-evaluate cycles even if the agent
# keeps proposing. Cost ceiling on a real loop.
MAX_ITERS = 10


def production_rule(ticket: dict) -> str:
    """Six-month-old hand-written rule. Only catches 'crash' for bugs."""
    title = (ticket.get("title") or "").lower()
    if "crash" in title:
        return "bug"
    if title.endswith("?"):
        return "question"
    return "feature_request"


def truth_oracle(ticket: dict) -> str:
    """Ground truth — what a perfect classifier would return.

    In production: a labeled validation set, a downstream signal
    (with a wrapper that waits for it), a reviewer-pool aggregator,
    or a language-model judge committee with bias guardrails.
    """
    title = (ticket.get("title") or "").lower()
    if any(kw in title for kw in _BUG_KEYWORDS_TRUTH):
        return "bug"
    if title.endswith("?"):
        return "question"
    return "feature_request"


def _make_keyword_rule(keywords: tuple[str, ...]) -> Callable[[dict], str]:
    """Factory: build a classifier that flags 'bug' on any of these keywords."""

    def rule(ticket: dict) -> str:
        title = (ticket.get("title") or "").lower()
        if any(kw in title for kw in keywords):
            return "bug"
        if title.endswith("?"):
            return "question"
        return "feature_request"

    return rule


# Sample tickets the loop evaluates against. In production this is
# live traffic; for the example we hard-code a representative mix.
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
] * 5  # repeat to give the harness statistical power


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _print_report(iter_num: int, report: CandidateReport) -> None:
    verdict = "PROMOTE" if report.recommend_promote else "HOLD  "
    print(
        f"iter {iter_num}: {report.candidate_name:14s}  "
        f"prod_acc={report.prod_accuracy:.1%}  "
        f"cand_acc={report.candidate_accuracy:.1%}  "
        f"b={report.b}  c={report.c}  p={report.p_value:.2e}  "
        f"-> {verdict}"
    )


if __name__ == "__main__":
    random.seed(0)
    main()
