# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Candidate harness for autoresearch loops.

The pattern this module supports:

1. An external loop (an autoresearch agent, an A/B-testing harness,
   a human running experiments) proposes candidate classifiers —
   variations of the production rule, alternative language-model prompts,
   fresh ML head architectures, different gate thresholds.
2. The candidate runs in shadow alongside the live switch's
   decision. Production traffic flows normally; the candidate's
   prediction is recorded but not used.
3. A truth oracle (labeled validation set, downstream signal,
   reviewer pool) provides ground truth.
4. After enough paired observations, the harness runs a
   head-to-head significance test
   (:class:`postrule.gates.McNemarGate` by default — McNemar's
   exact test under the hood) between the production decision
   and the candidate's prediction and returns a
   :class:`CandidateReport` with a recommendation.

The loop generates candidates; the harness gates promotion via
a head-to-head significance test; the underlying
:class:`LearnedSwitch`'s rule floor stays in place throughout.

See also
--------
:mod:`postrule.gates` — the McNemar implementation used here.
:mod:`postrule.verdicts` — VerdictSource family for sourcing truth
asynchronously (model judges, committees, webhooks, human reviewers).
``examples/19_autoresearch_loop.py`` — runnable end-to-end loop.
``docs/autoresearch.md`` — the full positioning + walkthrough.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from postrule.core import LearnedSwitch

# Default significance threshold for ``recommend_promote``. Matches
# the conventional 5% level used in the paper. Override per-call
# via ``CandidateHarness(alpha=...)``.
_DEFAULT_ALPHA = 0.05


@dataclass(frozen=True)
class CandidateReport:
    """Paired-McNemar verdict on whether a candidate beats production.

    Returned by :meth:`CandidateHarness.evaluate`. The autoresearch
    loop reads ``recommend_promote`` to decide whether to swap the
    candidate into production, ``p_value`` for the statistical
    confidence, and ``b`` / ``c`` for the discordant-pair counts the
    decision is grounded in.

    All counts cover only **paired observations** — inputs where
    both the production switch and the candidate produced a
    prediction and the truth oracle returned a label.
    """

    candidate_name: str
    paired_observations: int
    prod_correct: int
    candidate_correct: int
    b: int  # candidate right, prod wrong (= "candidate gain")
    c: int  # prod right, candidate wrong (= "candidate loss")
    discordant: int  # b + c (only these contribute to McNemar)
    p_value: float
    prod_accuracy: float
    candidate_accuracy: float
    alpha: float
    recommend_promote: bool

    def summary_line(self) -> str:
        """One-line human-readable summary suitable for log/CLI output."""
        verdict = "PROMOTE" if self.recommend_promote else "HOLD"
        return (
            f"[{verdict}] {self.candidate_name}: "
            f"prod={self.prod_accuracy:.1%} candidate={self.candidate_accuracy:.1%} "
            f"(n={self.paired_observations}, b={self.b}, c={self.c}, "
            f"p={self.p_value:.2e}, alpha={self.alpha:.2f})"
        )


@dataclass
class _Observation:
    """One paired record: (input, prod_label, candidate_label, truth)."""

    prod_label: Any
    candidate_label: Any
    true_label: Any
    prod_correct: bool
    candidate_correct: bool


def _mcnemar_p_value(b: int, c: int) -> float:
    """Two-sided exact-binomial p-value for paired McNemar.

    Counts are the discordant pairs: ``b`` cases where one
    classifier was right and the other was wrong (canonically
    "candidate right, prod wrong") and ``c`` the symmetric
    direction. The null hypothesis is that ``b`` and ``c`` come
    from the same Bernoulli(0.5) — equivalent to "the two
    classifiers err equally often on inputs they disagree on."

    Returns ``1.0`` when there are no discordant pairs (no
    evidence either way).
    """
    discordant = b + c
    if discordant == 0:
        return 1.0
    k = min(b, c)
    p_one = sum(math.comb(discordant, i) * (0.5**discordant) for i in range(k + 1))
    return min(1.0, 2 * p_one)


class CandidateHarness:
    """Shadow-evaluate candidate classifiers against a live switch.

    Wraps a :class:`LearnedSwitch` so an external loop can register
    candidate classifiers, route traffic through both production
    and the candidate(s), and get a head-to-head significance
    verdict on whether each candidate beats the live decision.
    (McNemar's exact test under the hood — see
    :class:`postrule.gates.McNemarGate`.)

    Parameters
    ----------
    switch
        The production switch. Its current decision is the baseline
        every candidate is compared against.
    truth_oracle
        ``Callable[[input], true_label]``. Returns the ground-truth
        label for an input. In a real autoresearch loop this is
        typically a held-out labeled validation set, a downstream
        signal that resolves later (with a wrapper that waits for
        it), a reviewer pool's verdict aggregator, or a high-quality
        language-model judge committee.
    alpha
        Significance level used to set ``recommend_promote``.
        Default ``0.05``. Pass a tighter ``0.01`` for high-stakes
        promotion decisions.
    on_promote_recommendation
        Optional callback fired the first time a candidate's
        :class:`CandidateReport` flips ``recommend_promote=True``.
        Useful for autoresearch loops that want to be notified the
        moment a candidate clears the bar rather than polling.

    Notes
    -----
    The harness is sync. Each ``observe(input)`` call runs the
    production switch's :meth:`classify` plus every registered
    candidate plus the truth oracle on the input — typically once
    per input the autoresearch loop wants to evaluate. For
    high-throughput loops, use :meth:`observe_batch` which is the
    same logic with a single registry walk.

    The harness deliberately does **not** modify the production
    switch. Candidates run alongside, never instead of. To actually
    promote a candidate to production, the autoresearch loop swaps
    it into the switch via the existing :class:`LearnedSwitch`
    surface (``sw._rule = candidate``, or replacing the
    ``ml_head``, etc.) — ideally guarded by your own deployment
    process. The harness's job is to tell the loop **when** the
    swap is statistically justified, not to perform it.
    """

    def __init__(
        self,
        switch: LearnedSwitch,
        truth_oracle: Callable[[Any], Any],
        *,
        alpha: float = _DEFAULT_ALPHA,
        on_promote_recommendation: Callable[[CandidateReport], None] | None = None,
    ) -> None:
        if not 0.0 < alpha < 1.0:
            raise ValueError(f"alpha must be in (0, 1); got {alpha}")
        if not callable(truth_oracle):
            raise TypeError("truth_oracle must be callable")
        self.switch = switch
        self._truth_oracle = truth_oracle
        self._alpha = alpha
        self._on_promote = on_promote_recommendation
        self._candidates: dict[str, Callable[[Any], Any]] = {}
        self._observations: dict[str, list[_Observation]] = {}
        self._already_recommended: set[str] = set()

    # --- Registry ----------------------------------------------------------

    def register(
        self,
        name: str,
        candidate: Callable[[Any], Any],
    ) -> None:
        """Add a candidate classifier to evaluate.

        ``name`` must be unique within the harness's lifetime;
        autoresearch loops typically encode the iteration number
        and a short hash (``"v3-7c4a2"``).

        ``candidate`` is any callable mapping an input to a label.
        That includes a pure rule, a language-model-driven function, an ML
        head's :meth:`predict` bound, or a more complex composite.
        """
        if not name:
            raise ValueError("candidate name cannot be empty")
        if name in self._candidates:
            raise ValueError(f"candidate {name!r} already registered")
        if not callable(candidate):
            raise TypeError("candidate must be callable")
        self._candidates[name] = candidate
        self._observations[name] = []

    def unregister(self, name: str) -> None:
        """Remove a candidate. Drops accumulated observations for it."""
        self._candidates.pop(name, None)
        self._observations.pop(name, None)
        self._already_recommended.discard(name)

    @property
    def names(self) -> list[str]:
        """Currently-registered candidate names (in registration order)."""
        return list(self._candidates)

    # --- Observation -------------------------------------------------------

    def observe(self, input: Any) -> None:
        """Run prod + every candidate + the truth oracle on one input.

        Captures paired correctness for each registered candidate.
        Observations accumulate until :meth:`evaluate` is called.
        Errors from the truth oracle propagate (the harness can't
        do anything useful without truth); errors from individual
        candidates are absorbed — that candidate's record gets a
        ``candidate_label=None`` (counted as wrong) so a flaky
        candidate can't tank the whole run.
        """
        prod_result = self.switch.classify(input)
        true_label = self._truth_oracle(input)
        prod_correct = prod_result.label == true_label
        for name, fn in self._candidates.items():
            try:
                cand_label = fn(input)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                cand_label = None
            cand_correct = cand_label is not None and cand_label == true_label
            self._observations[name].append(
                _Observation(
                    prod_label=prod_result.label,
                    candidate_label=cand_label,
                    true_label=true_label,
                    prod_correct=prod_correct,
                    candidate_correct=cand_correct,
                )
            )

    def observe_batch(self, inputs: Iterable[Any]) -> int:
        """Convenience: stream many inputs through :meth:`observe`.

        Returns the count of observations recorded per candidate
        (same for every candidate since they all see every input).
        """
        n = 0
        for inp in inputs:
            self.observe(inp)
            n += 1
        return n

    # --- Evaluation --------------------------------------------------------

    def evaluate(self, name: str) -> CandidateReport:
        """Compute the paired-McNemar report for one candidate.

        Inspects every accumulated observation for the candidate;
        counts ``b`` (candidate right while prod wrong) and ``c``
        (prod right while candidate wrong); computes the two-sided
        exact-binomial p-value on the discordant pairs; sets
        ``recommend_promote`` when ``p < alpha`` AND the candidate's
        observed accuracy strictly exceeds production's. The accuracy
        gate prevents a "statistically significant but worse"
        promotion from a noise-driven low ``b``-low ``c`` corner.
        """
        if name not in self._candidates:
            raise KeyError(f"unknown candidate {name!r}")
        obs = self._observations[name]
        n = len(obs)
        prod_correct = sum(1 for o in obs if o.prod_correct)
        cand_correct = sum(1 for o in obs if o.candidate_correct)
        b = sum(1 for o in obs if o.candidate_correct and not o.prod_correct)
        c = sum(1 for o in obs if o.prod_correct and not o.candidate_correct)
        p = _mcnemar_p_value(b, c)
        prod_acc = prod_correct / n if n else 0.0
        cand_acc = cand_correct / n if n else 0.0
        recommend = (p < self._alpha) and (cand_acc > prod_acc)
        report = CandidateReport(
            candidate_name=name,
            paired_observations=n,
            prod_correct=prod_correct,
            candidate_correct=cand_correct,
            b=b,
            c=c,
            discordant=b + c,
            p_value=p,
            prod_accuracy=prod_acc,
            candidate_accuracy=cand_acc,
            alpha=self._alpha,
            recommend_promote=recommend,
        )
        if recommend and name not in self._already_recommended:
            self._already_recommended.add(name)
            if self._on_promote is not None:
                try:
                    self._on_promote(report)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except BaseException:
                    pass
        return report

    def evaluate_all(self) -> list[CandidateReport]:
        """Evaluate every registered candidate. Returns reports sorted
        by (recommend_promote, p_value) — promotion-worthy first,
        ties broken by tightest p-value."""
        reports = [self.evaluate(name) for name in self._candidates]
        reports.sort(key=lambda r: (not r.recommend_promote, r.p_value))
        return reports

    def __iter__(self) -> Iterator[str]:
        return iter(self._candidates)

    def __len__(self) -> int:
        return len(self._candidates)

    def __contains__(self, name: str) -> bool:
        return name in self._candidates


# ---------------------------------------------------------------------------
# Tournament — N-way candidate selection (round-robin head-to-head)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TournamentReport:
    """Outcome of a :class:`Tournament` across N candidates.

    Read ``winner`` for the candidate that beat every other at
    ``p < alpha`` (or ``None`` if no candidate swept). Read
    ``unanimous`` to detect the degenerate case where every
    candidate produced identical predictions on every input — in
    which case the formula picking among them is moot and any of
    them is the right ship. Read ``pairwise_reports`` for the full
    head-to-head matrix when the result deserves explanation.
    """

    winner: str | None
    unanimous: bool
    alpha: float
    candidates: list[str]
    accuracies: dict[str, float]
    paired_observations: int
    pairwise_reports: dict[tuple[str, str], CandidateReport] = field(default_factory=dict)
    # Why winner is None, if applicable. One of:
    #   "" (winner is set)
    #   "no_candidate_swept" (no candidate beat every other at p<alpha)
    #   "tie_at_top" (multiple candidates tied for highest accuracy
    #                 with no significant pairwise edge)
    reason: str = ""

    def summary_table(self) -> str:
        """Printable summary — one line of header + one row per candidate."""
        lines = [
            f"Tournament: {len(self.candidates)} candidates, "
            f"n={self.paired_observations}, alpha={self.alpha:.2f}",
        ]
        if self.unanimous:
            lines.append(f"All candidates agreed on every input → winner: {self.winner}")
            return "\n".join(lines)
        lines.append(f"{'Candidate':24s}  {'Accuracy':>10s}  {'Verdict':>10s}")
        names_sorted = sorted(self.candidates, key=lambda n: -self.accuracies[n])
        for name in names_sorted:
            verdict = "WINNER" if name == self.winner else ""
            lines.append(f"{name:24s}  {self.accuracies[name]:>9.1%}  {verdict:>10s}")
        if self.winner is None:
            lines.append(f"\nNo winner ({self.reason}).")
        return "\n".join(lines)


class Tournament:
    """Round-robin head-to-head selection across N candidates.

    For picking the best-among-N classification candidates with
    statistical confidence — prompt variants, scoring formulas, ML
    head architectures, retrieval strategies, language model choices,
    composite-formula tie-breaks (the case that motivated this
    feature). Unlike :class:`CandidateHarness` which compares each
    candidate against a single production baseline, Tournament has
    no fixed baseline: every candidate runs head-to-head against
    every other.

    Decision rule:

    1. **Unanimity short-circuit.** If every candidate produced
       identical predictions on every input, the formula picking
       among them is moot — any candidate is the right ship. The
       first-registered candidate wins by convention; ``unanimous``
       is set ``True``.
    2. **Round-robin McNemar.** Otherwise, every ordered pair is
       evaluated head-to-head: candidate ``X`` beats candidate ``Y``
       when their head-to-head McNemar p-value is below ``alpha``
       *and* ``X``'s accuracy strictly exceeds ``Y``'s on the same
       inputs. The winner is the candidate that beats every other
       under this rule. If no candidate sweeps (e.g. cyclic wins, or
       insufficient data to clear ``alpha`` against the strongest
       challenger), ``winner=None`` and ``reason`` explains.

    Parameters
    ----------
    candidates
        ``dict[name, callable]``. Each callable maps an input to a
        label. Insertion order is preserved (Python 3.7+ dicts);
        the first key is used as a tiebreaker for unanimous results
        and as the canonical iteration order in summary tables.
    truth_oracle
        ``Callable[[Input], Label]`` — returns the ground-truth
        label for an input. In production this is a labeled
        validation set lookup, a downstream signal, a reviewer-pool
        aggregator, or a language-model judge committee with bias guardrails.
        Tournament does not accept a :class:`VerdictSource`
        directly — VerdictSources answer "is this label correct?",
        not "what is the correct label?", and the signatures don't
        compose. Wrap a VerdictSource explicitly if needed::

            def truth_from_judge(inp):
                # Pick a reference candidate's prediction, ask the
                # judge whether it's correct, treat agreement as
                # truth. This is a different operation than
                # ground-truth lookup — use only when you don't
                # have direct truth.
                ...

    alpha
        Significance level for pairwise gates. Default ``0.05``.

    Examples
    --------
    Pick among three composite-scoring formulas on a benchmark
    corpus (the verifier-default scenario this feature was built
    for)::

        from postrule import Tournament

        def pick_multiplicative(stats): ...
        def pick_above_chance(stats): ...
        def pick_format_weighted(stats): ...

        t = Tournament(
            candidates={
                "multiplicative":  pick_multiplicative,
                "above_chance":    pick_above_chance,
                "format_weighted": pick_format_weighted,
            },
            truth_oracle=ground_truth_pick_for_corpus,
            alpha=0.05,
        )
        t.observe_batch(corpus)
        report = t.evaluate()
        print(report.summary_table())
        if report.winner:
            ship_default(report.winner)
    """

    def __init__(
        self,
        candidates: dict[str, Callable[[Any], Any]],
        truth_oracle: Callable[[Any], Any],
        *,
        alpha: float = _DEFAULT_ALPHA,
    ) -> None:
        if len(candidates) < 2:
            raise ValueError(f"Tournament needs >= 2 candidates; got {len(candidates)}")
        if not 0.0 < alpha < 1.0:
            raise ValueError(f"alpha must be in (0, 1); got {alpha}")
        if not callable(truth_oracle):
            raise TypeError("truth_oracle must be callable")
        for name, fn in candidates.items():
            if not name:
                raise ValueError("candidate name cannot be empty")
            if not callable(fn):
                raise TypeError(f"candidate {name!r} must be callable")
        self._candidates = dict(candidates)
        self._truth_oracle = truth_oracle
        self._alpha = alpha
        # Per-candidate observations. All candidates see all inputs in
        # the same order, so observations[a][i] and observations[b][i]
        # are aligned on input i — required for paired McNemar.
        self._observations: dict[str, list[bool]] = {name: [] for name in candidates}
        self._n_observed = 0
        self._predictions: list[dict[str, Any]] = []  # for unanimity check

    @property
    def names(self) -> list[str]:
        """Candidate names in registration order."""
        return list(self._candidates)

    def observe(self, input: Any) -> None:
        """Run every candidate + truth oracle on one input.

        Truth-oracle errors propagate (no useful work without
        truth). Errors from individual candidates are absorbed —
        that candidate's record is marked wrong for the input so
        a flaky candidate can't tank the run.
        """
        true_label = self._truth_oracle(input)
        per_input_preds: dict[str, Any] = {}
        for name, fn in self._candidates.items():
            try:
                pred = fn(input)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:
                pred = None
            per_input_preds[name] = pred
            self._observations[name].append(pred is not None and pred == true_label)
        self._predictions.append(per_input_preds)
        self._n_observed += 1

    def observe_batch(self, inputs: Iterable[Any]) -> int:
        """Run :meth:`observe` over an iterable. Returns the count."""
        n = 0
        for inp in inputs:
            self.observe(inp)
            n += 1
        return n

    def evaluate(self) -> TournamentReport:
        """Compute the tournament outcome over accumulated observations."""
        n = self._n_observed
        names = list(self._candidates)
        accuracies = {name: (sum(self._observations[name]) / n if n else 0.0) for name in names}

        # Unanimity check — every candidate produced the same
        # prediction on every input.
        unanimous = bool(self._predictions) and all(
            len(set(per_input.values())) == 1 for per_input in self._predictions
        )
        if unanimous:
            return TournamentReport(
                winner=names[0],
                unanimous=True,
                alpha=self._alpha,
                candidates=names,
                accuracies=accuracies,
                paired_observations=n,
                pairwise_reports={},
                reason="",
            )

        # Round-robin pairwise McNemar.
        pairwise: dict[tuple[str, str], CandidateReport] = {}
        for challenger in names:
            for baseline in names:
                if challenger == baseline:
                    continue
                obs_chal = self._observations[challenger]
                obs_base = self._observations[baseline]
                b = sum(1 for i in range(n) if obs_chal[i] and not obs_base[i])
                c = sum(1 for i in range(n) if obs_base[i] and not obs_chal[i])
                p = _mcnemar_p_value(b, c)
                chal_correct = sum(obs_chal)
                base_correct = sum(obs_base)
                chal_acc = accuracies[challenger]
                base_acc = accuracies[baseline]
                recommend = (p < self._alpha) and (chal_acc > base_acc)
                pairwise[(challenger, baseline)] = CandidateReport(
                    candidate_name=challenger,
                    paired_observations=n,
                    prod_correct=base_correct,
                    candidate_correct=chal_correct,
                    b=b,
                    c=c,
                    discordant=b + c,
                    p_value=p,
                    prod_accuracy=base_acc,
                    candidate_accuracy=chal_acc,
                    alpha=self._alpha,
                    recommend_promote=recommend,
                )

        # Winner = the candidate that beats every other.
        winner = None
        reason = "no_candidate_swept"
        for name in names:
            beats_all = all(
                pairwise[(name, other)].recommend_promote for other in names if other != name
            )
            if beats_all:
                winner = name
                reason = ""
                break

        # If no statistical winner but exactly one candidate is
        # strictly ahead in raw accuracy, mark that as a tie-at-top
        # rather than a sweep failure — distinguishes "weak
        # evidence" from "tied performance."
        if winner is None and accuracies:
            top = max(accuracies.values())
            top_names = [n for n, a in accuracies.items() if a == top]
            if len(top_names) > 1:
                reason = "tie_at_top"

        return TournamentReport(
            winner=winner,
            unanimous=False,
            alpha=self._alpha,
            candidates=names,
            accuracies=accuracies,
            paired_observations=n,
            pairwise_reports=pairwise,
            reason=reason,
        )

    def __len__(self) -> int:
        return len(self._candidates)

    def __contains__(self, name: str) -> bool:
        return name in self._candidates

    def __iter__(self) -> Iterator[str]:
        return iter(self._candidates)


__all__ = [
    "CandidateHarness",
    "CandidateReport",
    "Tournament",
    "TournamentReport",
]
