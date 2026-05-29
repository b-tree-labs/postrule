# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: LicenseRef-BSL-1.1
#
# pick_quickstart_site is the heart of `postrule quickstart` — it picks the
# top auto-liftable site so the wizard can wrap something cleanly without
# manual hazard triage. If nothing's auto-liftable we surface None and the
# wizard sends the user to `postrule analyze` instead of guessing.

from postrule.analyzer import ClassificationSite
from postrule.cli import pick_quickstart_site


def _site(
    name: str,
    *,
    score: float = 1.0,
    lift_status: str = "auto_liftable",
    pattern: str = "P1",
) -> ClassificationSite:
    return ClassificationSite(
        file_path=f"{name}.py",
        function_name=name,
        line_start=1,
        line_end=10,
        pattern=pattern,
        labels=["a", "b"],
        label_cardinality=2,
        priority_score=score,
        lift_status=lift_status,
    )


class _Report:
    def __init__(self, sites):
        self.sites = sites


class TestPickQuickstartSite:
    def test_empty_report_returns_none(self):
        assert pick_quickstart_site(_Report([])) is None

    def test_picks_highest_priority_among_auto_liftable(self):
        sites = [
            _site("low", score=0.2),
            _site("winner", score=2.5),
            _site("mid", score=1.1),
        ]
        picked = pick_quickstart_site(_Report(sites))
        assert picked is not None
        assert picked.function_name == "winner"

    def test_skips_non_auto_liftable_even_if_higher_priority(self):
        # The wizard refuses to wrap a site with open hazards — better to
        # send the user to `postrule analyze` than to wrap something risky.
        sites = [
            _site("hazardous", score=9.9, lift_status="needs_review"),
            _site("clean", score=1.0, lift_status="auto_liftable"),
        ]
        picked = pick_quickstart_site(_Report(sites))
        assert picked is not None
        assert picked.function_name == "clean"

    def test_returns_none_when_nothing_is_auto_liftable(self):
        sites = [_site("a", lift_status="needs_review"), _site("b", lift_status="rejected")]
        assert pick_quickstart_site(_Report(sites)) is None


class TestCmdWizard:
    """Orchestration tests for cmd_wizard — mock the analyzer at its source so
    the lazy `from postrule.analyzer import analyze` inside cmd_wizard picks
    up the stubbed version.
    """

    def _args(self, path="."):
        import argparse

        return argparse.Namespace(path=path, yes=True)

    def test_no_sites_returns_1_with_helpful_message(self, monkeypatch, capsys):
        import postrule.analyzer
        from postrule.cli import cmd_wizard

        monkeypatch.setattr(postrule.analyzer, "analyze", lambda _p: _Report([]))
        rc = cmd_wizard(self._args())
        assert rc == 1
        err = capsys.readouterr().err
        assert "No classification sites" in err
        assert "postrule analyze" in err

    def test_only_hazardous_sites_returns_1_and_points_at_analyze(self, monkeypatch, capsys):
        import postrule.analyzer
        from postrule.cli import cmd_wizard

        sites = [
            _site("risky", score=9.9, lift_status="needs_review"),
            _site("rejected_one", lift_status="rejected"),
        ]
        monkeypatch.setattr(postrule.analyzer, "analyze", lambda _p: _Report(sites))
        rc = cmd_wizard(self._args())
        assert rc == 1
        err = capsys.readouterr().err
        assert "none are auto-liftable" in err
        assert "postrule analyze" in err
