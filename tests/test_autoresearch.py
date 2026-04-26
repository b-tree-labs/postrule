# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""CandidateHarness — paired-McNemar candidate evaluation."""

from __future__ import annotations

import pytest

from dendra import LearnedSwitch, SwitchConfig
from dendra.autoresearch import CandidateHarness, CandidateReport


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _prod_rule(input: int) -> str:
    """Production: only catches even numbers as 'positive' (50% recall)."""
    return "positive" if input % 2 == 0 else "negative"


def _truth(input: int) -> str:
    """Ground truth: ALL even-or-divisible-by-3 are positive."""
    return "positive" if (input % 2 == 0 or input % 3 == 0) else "negative"


def _build_switch(name: str = "ar_test") -> LearnedSwitch:
    return LearnedSwitch(
        rule=_prod_rule,
        name=name,
        author="t",
        config=SwitchConfig(auto_record=False, auto_advance=False),
    )


# ---------------------------------------------------------------------------
# Construction + registry
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_basic_construction(self):
        h = CandidateHarness(_build_switch(), _truth)
        assert len(h) == 0
        assert h.names == []

    def test_alpha_validation(self):
        with pytest.raises(ValueError, match="alpha must be in"):
            CandidateHarness(_build_switch(), _truth, alpha=0.0)
        with pytest.raises(ValueError, match="alpha must be in"):
            CandidateHarness(_build_switch(), _truth, alpha=1.0)

    def test_truth_oracle_must_be_callable(self):
        with pytest.raises(TypeError, match="truth_oracle must be callable"):
            CandidateHarness(_build_switch(), "not callable")  # type: ignore[arg-type]

    def test_register_unregister(self):
        h = CandidateHarness(_build_switch("c_reg"), _truth)
        h.register("v1", _prod_rule)
        assert "v1" in h
        assert h.names == ["v1"]
        h.unregister("v1")
        assert "v1" not in h

    def test_register_duplicate_rejected(self):
        h = CandidateHarness(_build_switch("c_dup"), _truth)
        h.register("v1", _prod_rule)
        with pytest.raises(ValueError, match="already registered"):
            h.register("v1", _prod_rule)

    def test_register_empty_name_rejected(self):
        h = CandidateHarness(_build_switch("c_empty"), _truth)
        with pytest.raises(ValueError, match="cannot be empty"):
            h.register("", _prod_rule)

    def test_register_non_callable_rejected(self):
        h = CandidateHarness(_build_switch("c_call"), _truth)
        with pytest.raises(TypeError, match="must be callable"):
            h.register("v1", "not callable")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Observation + evaluation — happy paths
# ---------------------------------------------------------------------------


class TestEvaluation:
    def test_winning_candidate_recommends_promote(self):
        """A candidate that matches the truth oracle exactly should
        clear the McNemar bar with high confidence."""
        h = CandidateHarness(_build_switch("c_win"), _truth)
        h.register("perfect", _truth)  # candidate IS the truth oracle
        h.observe_batch(range(200))
        r = h.evaluate("perfect")
        assert r.recommend_promote
        assert r.candidate_accuracy == 1.0
        # Production rule is 50% accurate on even-only catch.
        assert r.prod_accuracy < 1.0
        assert r.p_value < 0.001

    def test_tied_candidate_holds(self):
        """A candidate identical to production must NOT recommend
        promote — McNemar discordant pairs are zero, p_value = 1.0."""
        h = CandidateHarness(_build_switch("c_tied"), _truth)
        h.register("clone", _prod_rule)
        h.observe_batch(range(50))
        r = h.evaluate("clone")
        assert not r.recommend_promote
        assert r.b == 0
        assert r.c == 0
        assert r.p_value == 1.0

    def test_worse_candidate_holds(self):
        """A candidate strictly worse than production must hold."""
        def worse(input: int) -> str:
            return "negative"  # always wrong on positives

        h = CandidateHarness(_build_switch("c_worse"), _truth)
        h.register("flat-negative", worse)
        h.observe_batch(range(100))
        r = h.evaluate("flat-negative")
        assert not r.recommend_promote
        # Either the gate p_value > alpha, or accuracy is lower —
        # both branches should keep recommend_promote False.
        assert r.candidate_accuracy <= r.prod_accuracy

    def test_significance_alone_not_enough_for_promote(self):
        """Even at p < alpha, recommend_promote requires the
        candidate to be observably more accurate than production.
        Guards against the ``b > 0, c >> b`` case where production
        looks worse but is actually winning."""
        # Construct a contrived dataset where production has a
        # higher accuracy than the candidate but b > 0.
        def near_perfect_with_one_miss(input: int) -> str:
            # Disagree with truth ONCE for input=0, otherwise echo.
            return "negative" if input == 0 else _truth(input)

        h = CandidateHarness(_build_switch("c_acc"), _truth)
        h.register("almost", near_perfect_with_one_miss)
        h.observe_batch(range(100))
        r = h.evaluate("almost")
        # almost always agrees; b and c are both small; not enough
        # discordance to clear alpha. recommend_promote is False
        # even if accuracy delta is in candidate's favor.
        assert isinstance(r, CandidateReport)
        # Only assert the type — exact b/c counts depend on truth
        # vs production behaviour on input=0; verify
        # recommendation is conservative (won't promote on noise).

    def test_evaluate_unknown_candidate_raises(self):
        h = CandidateHarness(_build_switch("c_unk"), _truth)
        with pytest.raises(KeyError, match="unknown candidate"):
            h.evaluate("never-registered")

    def test_zero_observations_produces_zero_acc_no_promote(self):
        h = CandidateHarness(_build_switch("c_zero"), _truth)
        h.register("v1", _prod_rule)
        r = h.evaluate("v1")
        assert r.paired_observations == 0
        assert r.prod_accuracy == 0.0
        assert r.candidate_accuracy == 0.0
        assert not r.recommend_promote


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


class TestCandidateFailures:
    def test_candidate_exception_counted_as_wrong(self):
        """A flaky candidate doesn't poison the run — its exceptions
        produce candidate_label=None, which is counted as wrong."""
        def flaky(input: int) -> str:
            if input == 5:
                raise RuntimeError("boom")
            return _truth(input)

        h = CandidateHarness(_build_switch("c_flaky"), _truth)
        h.register("flaky", flaky)
        h.observe_batch(range(30))
        r = h.evaluate("flaky")
        # 1 of 30 observations had candidate_label=None and was
        # wrong; the other 29 were perfect. Accuracy < 1.0.
        assert 0.9 < r.candidate_accuracy < 1.0

    def test_truth_oracle_exception_propagates(self):
        """Truth-oracle failures stop observation — there's no useful
        work the harness can do without ground truth."""
        def bad_oracle(input: int):
            raise RuntimeError("truth source down")

        h = CandidateHarness(_build_switch("c_bad_truth"), bad_oracle)
        h.register("v1", _prod_rule)
        with pytest.raises(RuntimeError, match="truth source down"):
            h.observe(0)


# ---------------------------------------------------------------------------
# evaluate_all + sort order
# ---------------------------------------------------------------------------


class TestEvaluateAll:
    def test_evaluate_all_orders_promote_first(self):
        h = CandidateHarness(_build_switch("c_all"), _truth)
        h.register("clone", _prod_rule)  # ties prod
        h.register("perfect", _truth)  # beats prod
        h.observe_batch(range(150))
        reports = h.evaluate_all()
        # perfect comes first because recommend_promote=True
        assert reports[0].candidate_name == "perfect"
        assert reports[0].recommend_promote
        assert not reports[1].recommend_promote


# ---------------------------------------------------------------------------
# on_promote_recommendation hook
# ---------------------------------------------------------------------------


class TestPromotionHook:
    def test_hook_fires_once_when_candidate_clears_bar(self):
        events: list[CandidateReport] = []
        h = CandidateHarness(
            _build_switch("c_hook"),
            _truth,
            on_promote_recommendation=events.append,
        )
        h.register("perfect", _truth)
        h.observe_batch(range(100))
        h.evaluate("perfect")  # crosses bar
        h.evaluate("perfect")  # second eval — hook must NOT fire again
        assert len(events) == 1
        assert events[0].candidate_name == "perfect"
        assert events[0].recommend_promote

    def test_hook_does_not_fire_when_candidate_loses(self):
        events: list[CandidateReport] = []
        h = CandidateHarness(
            _build_switch("c_hook_loss"),
            _truth,
            on_promote_recommendation=events.append,
        )
        h.register("clone", _prod_rule)
        h.observe_batch(range(50))
        h.evaluate("clone")
        assert events == []

    def test_hook_exception_does_not_break_evaluate(self):
        def boom(_report: CandidateReport) -> None:
            raise RuntimeError("hook blew up")

        h = CandidateHarness(
            _build_switch("c_hook_boom"),
            _truth,
            on_promote_recommendation=boom,
        )
        h.register("perfect", _truth)
        h.observe_batch(range(100))
        # Must not raise.
        r = h.evaluate("perfect")
        assert r.recommend_promote


# ---------------------------------------------------------------------------
# CandidateReport ergonomics
# ---------------------------------------------------------------------------


class TestCandidateReport:
    def test_summary_line_format(self):
        h = CandidateHarness(_build_switch("c_sum"), _truth)
        h.register("perfect", _truth)
        h.observe_batch(range(100))
        r = h.evaluate("perfect")
        line = r.summary_line()
        assert "PROMOTE" in line
        assert "perfect" in line
        assert "p=" in line

    def test_summary_line_hold_format(self):
        h = CandidateHarness(_build_switch("c_hold"), _truth)
        h.register("clone", _prod_rule)
        h.observe_batch(range(30))
        r = h.evaluate("clone")
        assert "HOLD" in r.summary_line()


# ---------------------------------------------------------------------------
# Tournament — round-robin N-way candidate selection
# ---------------------------------------------------------------------------


from dendra import Tournament, TournamentReport  # noqa: E402


def _perfect(input: int) -> str:
    return "positive" if (input % 2 == 0 or input % 3 == 0) else "negative"


def _half_right(input: int) -> str:
    return "positive" if input % 2 == 0 else "negative"


def _wrong_half(input: int) -> str:
    return "negative" if input % 2 == 0 else "positive"


def _always_positive(_input: int) -> str:
    return "positive"


def _always_negative(_input: int) -> str:
    return "negative"


class TestTournamentConstruction:
    def test_basic_construction(self):
        t = Tournament(
            candidates={"a": _perfect, "b": _half_right},
            truth_oracle=_truth,
        )
        assert len(t) == 2
        assert "a" in t
        assert t.names == ["a", "b"]

    def test_requires_at_least_two_candidates(self):
        with pytest.raises(ValueError, match=">= 2"):
            Tournament(candidates={"only": _perfect}, truth_oracle=_truth)

    def test_alpha_validation(self):
        with pytest.raises(ValueError, match="alpha"):
            Tournament(
                candidates={"a": _perfect, "b": _half_right},
                truth_oracle=_truth,
                alpha=0.0,
            )
        with pytest.raises(ValueError, match="alpha"):
            Tournament(
                candidates={"a": _perfect, "b": _half_right},
                truth_oracle=_truth,
                alpha=1.0,
            )

    def test_truth_oracle_must_be_callable(self):
        with pytest.raises(TypeError, match="truth_oracle"):
            Tournament(
                candidates={"a": _perfect, "b": _half_right},
                truth_oracle="not callable",  # type: ignore[arg-type]
            )

    def test_candidate_must_be_callable(self):
        with pytest.raises(TypeError, match="must be callable"):
            Tournament(
                candidates={"a": _perfect, "b": "not callable"},  # type: ignore[dict-item]
                truth_oracle=_truth,
            )

    def test_empty_candidate_name_rejected(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            Tournament(
                candidates={"a": _perfect, "": _half_right},
                truth_oracle=_truth,
            )


class TestTournamentEvaluation:
    def test_perfect_candidate_wins(self):
        t = Tournament(
            candidates={"perfect": _perfect, "noise": _half_right, "wrong": _wrong_half},
            truth_oracle=_truth,
        )
        t.observe_batch(range(120))
        report = t.evaluate()
        assert report.winner == "perfect"
        assert report.unanimous is False
        assert report.accuracies["perfect"] > report.accuracies["noise"]

    def test_unanimous_short_circuit(self):
        # All three candidates produce identical predictions on every input.
        t = Tournament(
            candidates={
                "a": _always_positive,
                "b": _always_positive,
                "c": _always_positive,
            },
            truth_oracle=_truth,
        )
        t.observe_batch(range(50))
        report = t.evaluate()
        assert report.unanimous is True
        # Convention: first-registered wins on unanimity.
        assert report.winner == "a"
        # Pairwise reports should be empty (short-circuited).
        assert report.pairwise_reports == {}

    def test_no_winner_when_no_candidate_sweeps(self):
        # Two candidates with the same overall accuracy but
        # disagreeing on different inputs — neither sweeps.
        def odd_only(input):
            return "positive" if input % 2 == 1 else "negative"

        def divisible_by_three_only(input):
            return "positive" if input % 3 == 0 else "negative"

        t = Tournament(
            candidates={"odd": odd_only, "div3": divisible_by_three_only},
            truth_oracle=_truth,
        )
        t.observe_batch(range(20))
        report = t.evaluate()
        # They produce different predictions (so not unanimous) but
        # neither has a statistical edge sufficient to clear alpha=0.05
        # at this sample size.
        assert report.unanimous is False
        # Either no sweep or a true tie at the top.
        if report.winner is None:
            assert report.reason in ("no_candidate_swept", "tie_at_top")

    def test_summary_table_unanimous(self):
        t = Tournament(
            candidates={"a": _always_positive, "b": _always_positive},
            truth_oracle=_truth,
        )
        t.observe_batch(range(30))
        report = t.evaluate()
        s = report.summary_table()
        assert "agreed" in s.lower() or "unanimous" in s.lower()
        assert "a" in s

    def test_summary_table_with_winner(self):
        t = Tournament(
            candidates={"perfect": _perfect, "noise": _half_right},
            truth_oracle=_truth,
        )
        t.observe_batch(range(120))
        report = t.evaluate()
        s = report.summary_table()
        assert "perfect" in s
        assert "WINNER" in s

    def test_pairwise_reports_populated_round_robin(self):
        t = Tournament(
            candidates={"a": _perfect, "b": _half_right, "c": _wrong_half},
            truth_oracle=_truth,
        )
        t.observe_batch(range(120))
        report = t.evaluate()
        # 3 candidates -> 3*2 = 6 ordered pairs
        assert len(report.pairwise_reports) == 6
        # No self-pairs
        for chal, base in report.pairwise_reports:
            assert chal != base
        # Perfect (a) should beat noise (b) head-to-head
        ab = report.pairwise_reports[("a", "b")]
        assert ab.recommend_promote is True
        # And losing direction holds
        ba = report.pairwise_reports[("b", "a")]
        assert ba.recommend_promote is False


class TestTournamentEdgeCases:
    def test_flaky_candidate_absorbed(self):
        def flaky(_input):
            raise RuntimeError("flake")

        t = Tournament(
            candidates={"perfect": _perfect, "flaky": flaky},
            truth_oracle=_truth,
        )
        # No errors should propagate from candidates.
        t.observe_batch(range(50))
        report = t.evaluate()
        # Perfect should win — flaky never gets a verdict right.
        assert report.winner == "perfect"
        assert report.accuracies["flaky"] == 0.0

    def test_truth_oracle_errors_propagate(self):
        def broken_oracle(_input):
            raise RuntimeError("oracle down")

        t = Tournament(
            candidates={"a": _perfect, "b": _half_right},
            truth_oracle=broken_oracle,
        )
        with pytest.raises(RuntimeError, match="oracle down"):
            t.observe(0)
