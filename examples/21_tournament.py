# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
"""Picking among N candidate classifiers with statistical confidence.

Run: `python examples/21_tournament.py`

When you have several candidates that solve the same problem
differently — three prompt variants for a language model classifier, three
ML head architectures, three retrieval strategies, three scoring
formulas — the natural question is "which is best?" Naive accuracy
comparison promotes whichever happens to win on this particular
sample, including noise-level wins.

``Tournament`` is the head-to-head answer: every candidate runs on
the same inputs, every pair is evaluated for statistical
significance, and the winner is the candidate that beats every
other at ``p < alpha``. Unanimity short-circuit: if every
candidate produces identical predictions on every input, the
formula picking among them is moot and any of them is the right
ship.

This example uses three keyword-rule variants for ticket triage.
Replace the rules with prompt variants, ML heads, retrieval
strategies, or anything callable, and the rest of the loop is
unchanged.

What Tournament gives you, in one line each:

- *N-way head-to-head decision with bounded false-promotion.*
  Candidate has to beat every other candidate at the alpha you
  pass, not just the strongest one.
- *Unanimity short-circuit.* If candidates agreed everywhere,
  there's no statistical fork to resolve — Tournament reports
  that and skips the round-robin.
- *Round-robin pairwise reports.* The full ``CandidateReport``
  matrix is available for any decision that needs explanation.

For situations where each of these matters most — and where they
don't — see ``docs/scenarios.md``.
"""

from __future__ import annotations

from postrule import Tournament

# ---------------------------------------------------------------------------
# Three candidate triage rules — same task, different keyword strategies
# ---------------------------------------------------------------------------


def _narrow_rule(ticket: dict) -> str:
    """Conservative — only catches 'crash' as a bug."""
    title = (ticket.get("title") or "").lower()
    if "crash" in title:
        return "bug"
    if title.endswith("?"):
        return "question"
    return "feature_request"


def _moderate_rule(ticket: dict) -> str:
    """Adds 'error' to the bug-keyword list."""
    title = (ticket.get("title") or "").lower()
    if "crash" in title or "error" in title:
        return "bug"
    if title.endswith("?"):
        return "question"
    return "feature_request"


def _broad_rule(ticket: dict) -> str:
    """Catches the full bug-keyword family."""
    title = (ticket.get("title") or "").lower()
    if any(kw in title for kw in ("crash", "error", "down", "stuck", "broken")):
        return "bug"
    if title.endswith("?"):
        return "question"
    return "feature_request"


# ---------------------------------------------------------------------------
# Truth oracle and evaluation traffic
# ---------------------------------------------------------------------------


_BUG_KEYWORDS = ("crash", "error", "down", "stuck", "broken")


def truth_oracle(ticket: dict) -> str:
    """Ground-truth labeling. In production this is a labeled
    validation set, a downstream signal, or a language-model judge committee."""
    title = (ticket.get("title") or "").lower()
    if any(kw in title for kw in _BUG_KEYWORDS):
        return "bug"
    if title.endswith("?"):
        return "question"
    return "feature_request"


_TICKETS = [
    {"title": "app crashes on startup"},
    {"title": "got an error trying to log in"},
    {"title": "the search index is down"},
    {"title": "checkout is stuck on step 3"},
    {"title": "broken: cannot upload files"},
    {"title": "page crashes when I scroll"},
    {"title": "error 500 on dashboard refresh"},
    {"title": "service is down for our team"},
    {"title": "how do I export data?"},
    {"title": "is dark mode coming?"},
    {"title": "please add SAML support"},
    {"title": "add a CSV export option"},
    {"title": "i would love a dark theme"},
    {"title": "support for keyboard shortcuts"},
    {"title": "checkout error during payment"},
    {"title": "page is broken after the update"},
    {"title": "crash report attached"},
    {"title": "everything is down right now"},
    {"title": "feature: bulk-upload UI"},
    {"title": "add a setting for default view"},
] * 5  # repeat to give the gate statistical power


# ---------------------------------------------------------------------------
# The tournament
# ---------------------------------------------------------------------------


def main() -> None:
    t = Tournament(
        candidates={
            "narrow": _narrow_rule,
            "moderate": _moderate_rule,
            "broad": _broad_rule,
        },
        truth_oracle=truth_oracle,
        alpha=0.05,
    )
    t.observe_batch(_TICKETS)
    report = t.evaluate()

    print(report.summary_table())
    print()

    if report.unanimous:
        print(f"All candidates agreed → ship {report.winner}.")
    elif report.winner is not None:
        print(f"Winner: {report.winner} — ship it.")
        print("\nHead-to-head detail:")
        for (challenger, baseline), pair_report in sorted(report.pairwise_reports.items()):
            verdict = "wins" if pair_report.recommend_promote else "loses to"
            print(
                f"  {challenger:9s} {verdict:8s} {baseline:9s}  "
                f"acc={pair_report.candidate_accuracy:.1%} vs {pair_report.prod_accuracy:.1%}  "
                f"p={pair_report.p_value:.2e}"
            )
    else:
        print(f"No statistical winner ({report.reason}).")
        print("Either run more samples or accept that the candidates are tied.")


if __name__ == "__main__":
    main()
