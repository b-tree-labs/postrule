# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: LicenseRef-BSL-1.1
#
# Licensed under the Business Source License 1.1 (the "License").
# You may not use this file except in compliance with the License.
# You may obtain a copy of the License at LICENSE-BSL in the
# repository root, or at https://mariadb.com/bsl11/.
#
# Change Date:    2030-05-01
# Change License: Apache License, Version 2.0
#
# Additional Use Grant: see LICENSE-BSL. Production use is
# permitted; offering a competing hosted service is not.

"""Tests for the static analyzer."""

from __future__ import annotations

import json

from postrule.analyzer import analyze, render_json, render_text


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Pattern detection
# ---------------------------------------------------------------------------


class TestPatternP1:
    def test_if_elif_string_returns_matches(self, tmp_path):
        _write(
            tmp_path / "triage.py",
            "def triage(ticket):\n"
            "    t = ticket.get('title', '').lower()\n"
            "    if 'crash' in t:\n"
            "        return 'bug'\n"
            "    if '?' in t:\n"
            "        return 'question'\n"
            "    return 'feature'\n",
        )
        report = analyze(tmp_path)
        # P4 matches first (keyword-in-scan pattern), which is fine —
        # multiple patterns may apply; we report the first match.
        assert report.total_sites() == 1
        assert report.sites[0].function_name == "triage"
        assert set(report.sites[0].labels) == {"bug", "question", "feature"}
        assert report.sites[0].label_cardinality == 3
        assert report.sites[0].regime == "narrow"
        assert report.sites[0].priority_score > 0.0


class TestPatternP2:
    def test_match_case_matches(self, tmp_path):
        _write(
            tmp_path / "router.py",
            "def route(x):\n"
            "    match x:\n"
            "        case 'a':\n"
            "            return 'alpha'\n"
            "        case 'b':\n"
            "            return 'beta'\n"
            "        case _:\n"
            "            return 'default'\n",
        )
        report = analyze(tmp_path)
        assert report.total_sites() == 1
        s = report.sites[0]
        assert s.pattern == "P2"
        assert set(s.labels) == {"alpha", "beta", "default"}


class TestPatternP3:
    def test_dict_lookup_matches(self, tmp_path):
        _write(
            tmp_path / "lookup.py",
            "def map_code(code):\n"
            "    mapping = {\n"
            "        'a': 'alpha',\n"
            "        'b': 'beta',\n"
            "        'c': 'gamma',\n"
            "        'd': 'delta',\n"
            "    }\n"
            "    return mapping.get(code, 'unknown')\n",
        )
        report = analyze(tmp_path)
        assert report.total_sites() == 1
        assert report.sites[0].pattern == "P3"
        assert set(report.sites[0].labels) == {"alpha", "beta", "gamma", "delta", "unknown"}

    def test_ext_map_get_infers_labels_for_init(self, tmp_path):
        """SoilMetrix ingest_service._classify_file_type shape (postrule init)."""
        _write(
            tmp_path / "ingest.py",
            "def _classify_file_type(filename, data):\n"
            "    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''\n"
            "    ext_map = {\n"
            "        'csv': 'soil_test_csv',\n"
            "        'shp': 'boundary',\n"
            "        'pdf': 'document',\n"
            "    }\n"
            "    return ext_map.get(ext, 'unknown')\n",
        )
        report = analyze(tmp_path)
        assert report.total_sites() == 1
        s = report.sites[0]
        assert s.pattern == "P3"
        assert set(s.labels) == {"soil_test_csv", "boundary", "document", "unknown"}
        assert s.label_cardinality == 4
        assert s.regime == "narrow"


class TestPatternP4:
    def test_keyword_scanner_matches(self, tmp_path):
        _write(
            tmp_path / "scan.py",
            "def classify(text):\n"
            "    if 'error' in text:\n"
            "        return 'error'\n"
            "    if 'warning' in text:\n"
            "        return 'warning'\n"
            "    if 'info' in text:\n"
            "        return 'info'\n"
            "    return 'unknown'\n",
        )
        report = analyze(tmp_path)
        assert report.total_sites() == 1
        # P4 should be preferred over P1 here since the matchers run in order;
        # P1 is checked first. Either way, we get a valid site with labels.
        s = report.sites[0]
        assert s.pattern in ("P1", "P4")
        assert "error" in s.labels
        assert "warning" in s.labels


class TestPatternP5:
    def test_regex_dispatch_matches(self, tmp_path):
        _write(
            tmp_path / "regex_classify.py",
            "import re\n"
            "\n"
            "def classify(text):\n"
            "    if re.match(r'\\d+', text):\n"
            "        return 'numeric'\n"
            "    if re.search(r'[A-Z]+', text):\n"
            "        return 'uppercase'\n"
            "    return 'other'\n",
        )
        report = analyze(tmp_path)
        assert report.total_sites() == 1
        s = report.sites[0]
        # P1 applies (it's an if/elif with string returns), and runs first.
        # The regex ops inside are still covered by P5; the analyzer reports
        # the first matching pattern.
        assert s.pattern in ("P1", "P5")
        assert set(s.labels) == {"numeric", "uppercase", "other"}


class TestPatternP6:
    def test_model_prompted_classifier_matches(self, tmp_path):
        _write(
            tmp_path / "llm_classify.py",
            "def classify(text):\n"
            "    response = client.chat.completions.create(\n"
            "        model='gpt-4', messages=[{'role':'user','content':text}]\n"
            "    )\n"
            "    return response.choices[0].message.content.strip()\n",
        )
        report = analyze(tmp_path)
        assert report.total_sites() == 1
        assert report.sites[0].pattern == "P6"


# ---------------------------------------------------------------------------
# Non-classification functions are ignored
# ---------------------------------------------------------------------------


class TestNonMatches:
    def test_numeric_computation_not_matched(self, tmp_path):
        _write(
            tmp_path / "compute.py",
            "def add(a, b):\n    return a + b\n\ndef mul(a, b):\n    return a * b\n",
        )
        report = analyze(tmp_path)
        assert report.total_sites() == 0

    def test_single_return_string_not_classifier(self, tmp_path):
        # One string return alone doesn't make it a classifier — no branching.
        _write(
            tmp_path / "const.py",
            "def greeting():\n    return 'hello'\n",
        )
        report = analyze(tmp_path)
        assert report.total_sites() == 0

    def test_two_branches_but_no_strings(self, tmp_path):
        _write(
            tmp_path / "branch.py",
            "def route(x):\n    if x > 0:\n        return x + 1\n    return x - 1\n",
        )
        report = analyze(tmp_path)
        assert report.total_sites() == 0


# ---------------------------------------------------------------------------
# Directory traversal + ignore rules
# ---------------------------------------------------------------------------


class TestTraversal:
    def test_walks_subdirectories(self, tmp_path):
        _write(
            tmp_path / "a.py",
            "def triage(x):\n    if 'crash' in x: return 'bug'\n    return 'feat'\n",
        )
        _write(
            tmp_path / "sub" / "b.py",
            "def gate(x):\n"
            "    if 'pii' in x: return 'pii'\n"
            "    if 'tox' in x: return 'toxic'\n"
            "    return 'safe'\n",
        )
        report = analyze(tmp_path)
        assert report.total_sites() == 2
        files = sorted({s.file_path for s in report.sites})
        assert files == ["a.py", "sub/b.py"]

    def test_ignores_default_dirs(self, tmp_path):
        _write(
            tmp_path / "real.py",
            "def triage(x):\n    if 'a' in x: return 'x'\n    return 'y'\n",
        )
        _write(
            tmp_path / ".venv" / "noise.py",
            "def noise(x):\n    if 'a' in x: return 'x'\n    return 'y'\n",
        )
        _write(
            tmp_path / "__pycache__" / "gunk.py",
            "def gunk(x):\n    if 'a' in x: return 'x'\n    return 'y'\n",
        )
        report = analyze(tmp_path)
        files = {s.file_path for s in report.sites}
        assert files == {"real.py"}

    def test_parse_error_becomes_warning(self, tmp_path):
        _write(tmp_path / "good.py", "def f(x):\n    return x\n")
        _write(
            tmp_path / "bad.py",
            "def broken(x)\n"  # missing colon
            "    return 'a'\n",
        )
        report = analyze(tmp_path)
        assert len(report.errors) == 1
        assert "bad.py" in report.errors[0]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


class TestRender:
    def test_text_report_contains_file_lines(self, tmp_path):
        _write(
            tmp_path / "triage.py",
            "def triage(x):\n    if 'crash' in x: return 'bug'\n    return 'feature'\n",
        )
        report = analyze(tmp_path)
        text = render_text(report)
        assert "triage.py" in text
        assert "triage" in text
        assert "labels" in text.lower()
        assert "regime" in text.lower()

    def test_json_report_roundtrips(self, tmp_path):
        _write(
            tmp_path / "triage.py",
            "def triage(x):\n    if 'crash' in x: return 'bug'\n    return 'feature'\n",
        )
        report = analyze(tmp_path)
        payload = json.loads(render_json(report))
        assert payload["total_sites"] == 1
        assert payload["sites"][0]["function_name"] == "triage"
        assert set(payload["sites"][0]["labels"]) == {"bug", "feature"}

    def test_empty_report_message(self, tmp_path):
        _write(tmp_path / "plain.py", "def noop(): pass\n")
        report = analyze(tmp_path)
        text = render_text(report)
        assert "No classification sites" in text


# ---------------------------------------------------------------------------
# Markdown rendering + savings projection
# ---------------------------------------------------------------------------


class TestRenderMarkdown:
    def test_markdown_contains_ranked_table(self, tmp_path):
        from postrule.analyzer import render_markdown

        _write(
            tmp_path / "triage.py",
            "def triage(x):\n"
            "    if 'crash' in x: return 'bug'\n"
            "    if 'feat' in x: return 'feature'\n"
            "    return 'question'\n",
        )
        report = analyze(tmp_path)
        md = render_markdown(report)
        assert "# Postrule analyzer report" in md
        assert "Sites ranked by wrap priority" in md
        assert "`triage.py:1`" in md
        assert "`triage`" in md

    def test_markdown_empty_has_helpful_message(self, tmp_path):
        from postrule.analyzer import render_markdown

        _write(tmp_path / "noop.py", "def noop(): pass\n")
        report = analyze(tmp_path)
        md = render_markdown(report)
        assert "No classification sites identified" in md
        assert "file an issue" in md


class TestSavingsProjection:
    def test_projection_totals_are_positive(self, tmp_path):
        from postrule.analyzer import project_savings

        _write(
            tmp_path / "triage.py",
            "def triage(x):\n"
            "    if 'crash' in x: return 'bug'\n"
            "    if 'feat' in x: return 'feature'\n"
            "    return 'question'\n",
        )
        report = analyze(tmp_path)
        projections = project_savings(report)
        assert len(projections) == 1
        p = projections[0]
        assert p.total_low_usd > 0
        assert p.total_high_usd >= p.total_low_usd
        assert p.engineering_savings_low_usd > 0
        assert p.token_savings_low_usd > 0
        assert p.regression_avoidance_low_usd > 0

    def test_projection_respects_custom_assumptions(self, tmp_path):
        from postrule.analyzer import project_savings

        _write(
            tmp_path / "triage.py",
            "def triage(x):\n"
            "    if 'crash' in x: return 'bug'\n"
            "    if 'feat' in x: return 'feature'\n"
            "    return 'question'\n",
        )
        report = analyze(tmp_path)
        default = project_savings(report)[0]
        cheap = project_savings(report, eng_cost_per_week_usd=1_000.0)[0]
        assert cheap.engineering_savings_low_usd < default.engineering_savings_low_usd

    def test_markdown_with_projections_shows_totals(self, tmp_path):
        from postrule.analyzer import project_savings, render_markdown

        _write(
            tmp_path / "triage.py",
            "def triage(x):\n"
            "    if 'crash' in x: return 'bug'\n"
            "    if 'feat' in x: return 'feature'\n"
            "    return 'question'\n",
        )
        report = analyze(tmp_path)
        projections = project_savings(report)
        md = render_markdown(report, projections=projections)
        assert "Projected annual value by site" in md
        assert "Portfolio projected value" in md
        assert "$" in md


# ---------------------------------------------------------------------------
# Regime classification (paper §6 alignment)
# ---------------------------------------------------------------------------


class TestRegimeClassification:
    """``_classify_regime`` thresholds align with paper §6:
    cardinality < 30 → narrow (Regime A); 30..60 → medium;
    > 60 → high (Regime B); 0 → unknown.
    """

    def test_zero_cardinality_is_unknown(self):
        from postrule.analyzer import _classify_regime

        assert _classify_regime(0) == "unknown"

    def test_just_below_narrow_threshold(self):
        from postrule.analyzer import _classify_regime

        assert _classify_regime(1) == "narrow"
        assert _classify_regime(29) == "narrow"

    def test_narrow_threshold_boundary(self):
        """Cardinality 30 is the first medium; <30 is narrow."""
        from postrule.analyzer import _classify_regime

        assert _classify_regime(29) == "narrow"
        assert _classify_regime(30) == "medium"

    def test_medium_band(self):
        from postrule.analyzer import _classify_regime

        assert _classify_regime(30) == "medium"
        assert _classify_regime(45) == "medium"
        assert _classify_regime(60) == "medium"

    def test_high_threshold_boundary(self):
        """Cardinality 61 is the first high; ≤60 is medium."""
        from postrule.analyzer import _classify_regime

        assert _classify_regime(60) == "medium"
        assert _classify_regime(61) == "high"

    def test_far_above_high_threshold(self):
        from postrule.analyzer import _classify_regime

        assert _classify_regime(77) == "high"
        assert _classify_regime(151) == "high"
        assert _classify_regime(1000) == "high"

    def test_paper_section_6_anchors(self):
        """The paper's §6 heuristics use these boundary cases. Pin them."""
        from postrule.analyzer import _classify_regime

        # ATIS: 26 labels → narrow (Regime A)
        assert _classify_regime(26) == "narrow"
        # HWU64: 64 labels → high (Regime B)
        assert _classify_regime(64) == "high"
        # Banking77: 77 labels → high (Regime B)
        assert _classify_regime(77) == "high"
        # CLINC150: 151 labels → high (Regime B)
        assert _classify_regime(151) == "high"


class TestGateFitBoundaries:
    """``_compute_gate_fit`` rewards the Regime A sweet spot (2..29)
    plus narrow/medium regime plus P1/P4 patterns."""

    def test_min_score_no_labels_no_p1p4(self):
        from postrule.analyzer import _compute_gate_fit

        # Cardinality 0 → unknown regime, no sweet-spot bonus, P5 pattern.
        assert _compute_gate_fit([], "P5") == 2.0

    def test_max_score_p1_narrow_sweet_spot(self):
        from postrule.analyzer import _compute_gate_fit

        labels = ["bug", "feature", "question"]  # 3 labels, narrow, sweet spot
        assert _compute_gate_fit(labels, "P1") == 5.0

    def test_max_score_p4_narrow_sweet_spot(self):
        from postrule.analyzer import _compute_gate_fit

        labels = ["bug", "feature", "question"]
        assert _compute_gate_fit(labels, "P4") == 5.0

    def test_high_cardinality_loses_sweet_spot_and_regime_bonus(self):
        from postrule.analyzer import _compute_gate_fit

        labels = [f"label_{i}" for i in range(70)]  # high regime, outside sweet spot
        # 2 base + 0 (not in sweet spot) + 0 (high regime) + 1 (P1) = 3.0
        assert _compute_gate_fit(labels, "P1") == 3.0

    def test_medium_regime_loses_sweet_spot_keeps_regime_bonus(self):
        from postrule.analyzer import _compute_gate_fit

        labels = [f"label_{i}" for i in range(40)]  # medium regime, outside sweet spot
        # 2 base + 0 (not sweet spot, n>=30) + 1 (medium regime) + 1 (P4) = 4.0
        assert _compute_gate_fit(labels, "P4") == 4.0

    def test_p2_pattern_no_pattern_bonus(self):
        from postrule.analyzer import _compute_gate_fit

        labels = ["bug", "feature"]  # narrow, sweet spot, but P2 not P1/P4
        # 2 base + 1 (sweet spot) + 1 (narrow) + 0 (P2) = 4.0
        assert _compute_gate_fit(labels, "P2") == 4.0


class TestVolumeEstimate:
    """``_compute_volume_estimate`` derives cold/warm/hot from AST signals."""

    def _parse_fn(self, src: str):
        import ast as _ast

        mod = _ast.parse(src)
        return next(n for n in _ast.walk(mod) if isinstance(n, _ast.FunctionDef))

    def test_warm_default(self):
        from postrule.analyzer import _compute_volume_estimate

        fn = self._parse_fn("def f(x):\n    return 'a'\n")
        assert _compute_volume_estimate(fn, "src/svc/router.py") == "warm"

    def test_hot_via_route_decorator_attribute(self):
        from postrule.analyzer import _compute_volume_estimate

        fn = self._parse_fn("@app.post('/triage')\ndef triage(req):\n    return 'bug'\n")
        assert _compute_volume_estimate(fn, "src/svc/api.py") == "hot"

    def test_hot_via_route_decorator_bare_name(self):
        from postrule.analyzer import _compute_volume_estimate

        fn = self._parse_fn("@route\ndef view(req):\n    return 'ok'\n")
        assert _compute_volume_estimate(fn, "src/views.py") == "hot"

    def test_cold_via_cli_path(self):
        from postrule.analyzer import _compute_volume_estimate

        fn = self._parse_fn("def cmd_init(args):\n    return 'ok'\n")
        assert _compute_volume_estimate(fn, "src/postrule/cli.py") == "cold"

    def test_cold_via_migrations_path(self):
        from postrule.analyzer import _compute_volume_estimate

        fn = self._parse_fn("def upgrade():\n    return 'done'\n")
        assert _compute_volume_estimate(fn, "src/db/migrations/0042_add.py") == "cold"


class TestPriorityScore:
    """``_compute_priority_score`` blends gate-fit, volume, and lift."""

    def test_max_priority_hot_auto_liftable(self):
        from postrule.analyzer import _compute_priority_score

        # gate_fit 5 × volume 1.0 × lift 1.0 → 5.0
        assert _compute_priority_score(5.0, "hot", "auto_liftable") == 5.0

    def test_warm_auto_liftable_takes_volume_haircut(self):
        from postrule.analyzer import _compute_priority_score

        # gate_fit 5 × volume 0.7 × lift 1.0 → 3.5
        assert _compute_priority_score(5.0, "warm", "auto_liftable") == 3.5

    def test_cold_refused_deeply_deprioritized(self):
        from postrule.analyzer import _compute_priority_score

        # gate_fit 5 × volume 0.4 × lift 0.3 → 0.6
        assert _compute_priority_score(5.0, "cold", "refused") == 0.6

    def test_already_dendrified_zeroed_out(self):
        from postrule.analyzer import _compute_priority_score

        # No remaining work to prioritize on a site already wrapped.
        assert _compute_priority_score(5.0, "hot", "already_dendrified") == 0.0


class TestCohortComparisonLine:
    """``render_text`` emits a cohort-comparison line when cohort signal
    is real (cohort_size >= 10 + median field populated)."""

    def _patch_defaults(self, monkeypatch, *, cohort_size, median):
        from postrule.insights.tuned_defaults import TunedDefaults

        defaults = TunedDefaults(
            cohort_size=cohort_size,
            median_high_priority_density=median,
        )

        def _stub():
            return defaults

        # Patch via the import that analyzer.py uses lazily.
        import postrule.insights as _i

        monkeypatch.setattr(_i, "load_cached_or_baked_in", _stub)

    def _build_report_with_priorities(self, priorities):
        from postrule.analyzer import AnalyzerReport, ClassificationSite

        sites = [
            ClassificationSite(
                file_path=f"f{i}.py",
                function_name=f"fn{i}",
                line_start=1,
                line_end=2,
                pattern="P1",
                labels=["a", "b"],
                label_cardinality=2,
                regime="narrow",
                volume_estimate="warm",
                priority_score=p,
                lift_status="auto_liftable",
            )
            for i, p in enumerate(priorities)
        ]
        return AnalyzerReport(root="/r", files_scanned=len(sites), sites=sites)

    def test_suppressed_when_cohort_too_small(self, monkeypatch):
        from postrule.analyzer import render_text

        self._patch_defaults(monkeypatch, cohort_size=9, median=0.30)
        report = self._build_report_with_priorities([5.0, 4.5, 2.0, 1.0])
        out = render_text(report)
        assert "Cohort comparison" not in out

    def test_suppressed_when_median_missing(self, monkeypatch):
        from postrule.analyzer import render_text

        self._patch_defaults(monkeypatch, cohort_size=50, median=None)
        report = self._build_report_with_priorities([5.0, 4.5, 2.0])
        out = render_text(report)
        assert "Cohort comparison" not in out

    def test_emits_above_median_when_density_higher(self, monkeypatch):
        from postrule.analyzer import render_text

        # 3 of 4 sites are high-priority (75%); cohort median 30%.
        self._patch_defaults(monkeypatch, cohort_size=47, median=0.30)
        report = self._build_report_with_priorities([5.0, 4.5, 4.2, 1.0])
        out = render_text(report)
        assert "Cohort comparison (n=47 deployments)" in out
        assert "75%" in out
        assert "30%" in out
        assert "above median" in out

    def test_emits_at_or_below_median_when_density_lower(self, monkeypatch):
        from postrule.analyzer import render_text

        # 0 of 3 sites are high-priority (0%); cohort median 30%.
        self._patch_defaults(monkeypatch, cohort_size=47, median=0.30)
        report = self._build_report_with_priorities([3.5, 2.0, 1.0])
        out = render_text(report)
        assert "Cohort comparison" in out
        assert "at or below median" in out


class TestRenderTextLoginNudge:
    """``render_text`` invites signup at the moment a fresh visitor sees
    concrete value (sites found in their own code). Signed-in users do
    not see the nudge — the upside is already theirs."""

    def _build_report(self):
        from postrule.analyzer import AnalyzerReport, ClassificationSite

        return AnalyzerReport(
            root="/r",
            files_scanned=1,
            sites=[
                ClassificationSite(
                    file_path="f.py",
                    function_name="fn",
                    line_start=1,
                    line_end=2,
                    pattern="P1",
                    labels=["a", "b"],
                    label_cardinality=2,
                    regime="narrow",
                    volume_estimate="warm",
                    priority_score=5.0,
                    lift_status="auto_liftable",
                )
            ],
        )

    def test_nudge_emitted_when_signed_out(self, monkeypatch):
        import postrule.auth as _auth
        from postrule.analyzer import render_text

        monkeypatch.setattr(_auth, "is_logged_in", lambda: False)
        out = render_text(self._build_report())
        assert "postrule login" in out
        assert "GitHub OAuth, no card" in out

    def test_nudge_suppressed_when_signed_in(self, monkeypatch):
        import postrule.auth as _auth
        from postrule.analyzer import render_text

        monkeypatch.setattr(_auth, "is_logged_in", lambda: True)
        out = render_text(self._build_report())
        assert "postrule login" not in out

    def test_nudge_absorbs_auth_exception(self, monkeypatch):
        import postrule.auth as _auth
        from postrule.analyzer import render_text

        def boom():
            raise OSError("creds file corrupt")

        monkeypatch.setattr(_auth, "is_logged_in", boom)
        out = render_text(self._build_report())
        # Either the nudge is suppressed or surfaced — both fine — but
        # render_text must not raise.
        assert isinstance(out, str)

    def test_no_nudge_on_empty_report(self, monkeypatch):
        import postrule.auth as _auth
        from postrule.analyzer import AnalyzerReport, render_text

        monkeypatch.setattr(_auth, "is_logged_in", lambda: False)
        empty = AnalyzerReport(root="/r", files_scanned=0, sites=[])
        out = render_text(empty)
        # Empty-report path returns before the nudge block.
        assert "postrule login" not in out


class TestInternalSwitchWraps:
    """Postrule-on-Postrule: ``_classify_pattern`` and ``_classify_lift_status``
    are wrapped with ``@ml_switch`` at Phase.RULE.

    Behavior at Phase.RULE must be bit-for-bit identical to the inline
    code these wraps replaced — same inputs → same labels.
    """

    def test_classify_pattern_p1_match(self):
        import ast as _ast

        from postrule.analyzer import _classify_pattern

        node = _ast.parse(
            "def triage(t):\n"
            "    if 'crash' in t: return 'bug'\n"
            "    if 'feat' in t: return 'feature'\n"
            "    return 'other'\n"
        ).body[0]
        assert _classify_pattern(node) == "P1"

    def test_classify_pattern_no_match_for_non_classifier(self):
        import ast as _ast

        from postrule.analyzer import _classify_pattern

        node = _ast.parse("def add(a, b):\n    return a + b\n").body[0]
        assert _classify_pattern(node) == "no_match"

    def test_classify_pattern_is_ml_switch_wrapped(self):
        from postrule.analyzer import _classify_pattern

        # Exposes the LearnedSwitch surface — proves the @ml_switch
        # decorator is applied (not just a plain function).
        assert hasattr(_classify_pattern, "status")
        st = _classify_pattern.status()
        assert st.name == "_classify_pattern"
        assert str(st.phase) == "Phase.RULE"

    def test_classify_lift_status_auto_when_no_hazards(self):
        from postrule.analyzer import _classify_lift_status

        assert _classify_lift_status([]) == "auto_liftable"

    def test_classify_lift_status_refused_on_error_hazard(self):
        from postrule.analyzer import Hazard, _classify_lift_status

        hazards = [
            Hazard(
                category="side_effect_evidence",
                line=10,
                reason="mutates self",
                suggested_fix="refactor",
                severity="error",
            )
        ]
        assert _classify_lift_status(hazards) == "refused"

    def test_classify_lift_status_needs_annotation_on_warning(self):
        from postrule.analyzer import Hazard, _classify_lift_status

        hazards = [
            Hazard(
                category="dynamic_dispatch",
                line=10,
                reason="dispatch via getattr",
                suggested_fix="add @evidence_inputs",
                severity="warn",
            )
        ]
        assert _classify_lift_status(hazards) == "needs_annotation"

    def test_classify_lift_status_is_ml_switch_wrapped(self):
        from postrule.analyzer import _classify_lift_status

        assert hasattr(_classify_lift_status, "status")
        st = _classify_lift_status.status()
        assert st.name == "_classify_lift_status"
        assert str(st.phase) == "Phase.RULE"

    def test_postrule_repo_scan_finds_internal_switches_already_wrapped(self, tmp_path):
        """Scanning the analyzer module itself surfaces both internal
        switches in ``already_dendrified`` — the dogfood story.
        """
        from pathlib import Path as _Path

        # Find the real analyzer.py by inspecting the import
        import postrule.analyzer as _a
        from postrule.analyzer import analyze

        analyzer_path = _Path(_a.__file__)
        report = analyze(analyzer_path)
        names = {fn for (_path, fn, _line) in report.already_dendrified}
        assert "_classify_pattern" in names
        assert "_classify_lift_status" in names


class TestSortSites:
    """``AnalyzerReport.sort_sites`` orders by the requested key."""

    def _report_with_three_sites(self):
        from postrule.analyzer import AnalyzerReport, ClassificationSite

        a = ClassificationSite(
            file_path="z_last.py",
            function_name="a",
            line_start=10,
            line_end=20,
            pattern="P3",
            labels=["x", "y"],
            label_cardinality=2,
            regime="medium",
            volume_estimate="cold",
            priority_score=1.5,
            lift_status="refused",
        )
        b = ClassificationSite(
            file_path="a_first.py",
            function_name="b",
            line_start=5,
            line_end=15,
            pattern="P1",
            labels=["x", "y", "z"],
            label_cardinality=3,
            regime="narrow",
            volume_estimate="hot",
            priority_score=5.0,
            lift_status="auto_liftable",
        )
        c = ClassificationSite(
            file_path="m_mid.py",
            function_name="c",
            line_start=1,
            line_end=8,
            pattern="P2",
            labels=["x"],
            label_cardinality=1,
            regime="narrow",
            volume_estimate="warm",
            priority_score=3.0,
            lift_status="needs_annotation",
        )
        return AnalyzerReport(root="/r", files_scanned=3, sites=[a, b, c])

    def test_priority_default_descending(self):
        r = self._report_with_three_sites()
        order = [s.function_name for s in r.sort_sites()]
        assert order == ["b", "c", "a"]  # 5.0, 3.0, 1.5

    def test_location_ascending(self):
        r = self._report_with_three_sites()
        order = [s.file_path for s in r.sort_sites(key="location")]
        assert order == ["a_first.py", "m_mid.py", "z_last.py"]

    def test_pattern_ascending(self):
        r = self._report_with_three_sites()
        order = [s.pattern for s in r.sort_sites(key="pattern")]
        assert order == ["P1", "P2", "P3"]

    def test_regime_narrow_first(self):
        r = self._report_with_three_sites()
        order = [s.regime for s in r.sort_sites(key="regime")]
        # narrow×2 first (priority desc within), then medium
        assert order == ["narrow", "narrow", "medium"]

    def test_lift_auto_first(self):
        r = self._report_with_three_sites()
        order = [s.lift_status for s in r.sort_sites(key="lift")]
        assert order == ["auto_liftable", "needs_annotation", "refused"]

    def test_reverse_flips_order(self):
        r = self._report_with_three_sites()
        forward = [s.function_name for s in r.sort_sites()]
        backward = [s.function_name for s in r.sort_sites(reverse=True)]
        assert backward == list(reversed(forward))

    def test_unknown_sort_key_raises(self):
        import pytest as _pytest

        r = self._report_with_three_sites()
        with _pytest.raises(ValueError, match="unknown sort key"):
            r.sort_sites(key="bogus")


class TestRegimeInJsonReport:
    """Regime field round-trips correctly through render_json."""

    def test_narrow_regime_in_json(self, tmp_path):
        _write(
            tmp_path / "triage.py",
            "def triage(x):\n"
            "    if 'a' in x: return 'bug'\n"
            "    if 'b' in x: return 'feature'\n"
            "    return 'question'\n",
        )
        report = analyze(tmp_path)
        out = json.loads(render_json(report))
        assert out["sites"][0]["regime"] == "narrow"
        assert out["sites"][0]["label_cardinality"] == 3

    def test_high_regime_appears_in_text_summary(self, tmp_path):
        # Synthesize a 70-label classifier to trigger the high regime.
        labels = [f'"label_{i}"' for i in range(70)]
        body_lines = ["    if 'a' in x: return " + lbl for lbl in labels[:69]]
        body_lines.append(f"    return {labels[69]}")
        _write(
            tmp_path / "many.py",
            "def many(x):\n" + "\n".join(body_lines) + "\n",
        )
        report = analyze(tmp_path)
        text = render_text(report)
        # The text report's by-regime section should mention "high".
        assert "high" in text.lower()


# ---------------------------------------------------------------------------
# Async classifier sites — analyzer must surface AsyncFunctionDef matches.
# Real-codebase testing on 2026-04-28 found zero async sites across 10
# corpora including langchain. Root cause: ``isinstance(node, ast.FunctionDef)``
# excludes ``AsyncFunctionDef``. The fix surfaces them; lifters keep
# refusing for now (queued as v1.5 work).
# ---------------------------------------------------------------------------


class TestAsyncFunctionDef:
    def test_analyzer_recognizes_async_def(self, tmp_path):
        _write(
            tmp_path / "async_triage.py",
            "async def classify(text: str) -> str:\n"
            "    if 'bug' in text:\n"
            "        return 'bug'\n"
            "    return 'other'\n",
        )
        report = analyze(tmp_path)
        assert report.total_sites() == 1, (
            f"expected analyzer to surface 1 async classifier site, "
            f"got {report.total_sites()}: {[s.function_name for s in report.sites]}"
        )
        site = report.sites[0]
        assert site.function_name == "classify"
        assert site.pattern == "P1"
        assert site.priority_score > 0.0
        assert set(site.labels) == {"bug", "other"}

    def test_analyzer_finds_both_sync_and_async_in_same_file(self, tmp_path):
        _write(
            tmp_path / "mixed.py",
            "def sync_classify(text):\n"
            "    if 'a' in text: return 'alpha'\n"
            "    return 'beta'\n"
            "\n"
            "async def async_classify(text):\n"
            "    if 'a' in text: return 'alpha'\n"
            "    return 'beta'\n",
        )
        report = analyze(tmp_path)
        names = sorted(s.function_name for s in report.sites)
        assert names == ["async_classify", "sync_classify"]


# ---------------------------------------------------------------------------
# Test-path demotion — analyzer should mark sites in test directories /
# pytest-style fixtures as refused with a ``not_a_classifier`` hazard so
# they don't dominate fit lists when users run ``postrule analyze`` on
# their own repo. Mirrors the existing landing-corpus filter, applied at
# the analyzer layer.
# ---------------------------------------------------------------------------


class TestTestPathDemotion:
    def test_analyzer_demotes_test_function_sites(self, tmp_path):
        _write(
            tmp_path / "tests" / "test_thing.py",
            "def test_cors():\n"
            "    if 'origin' in 'header': return 'allowed'\n"
            "    return 'denied'\n",
        )
        report = analyze(tmp_path)
        # Site is still surfaced (option (b) — transparency over silence).
        assert report.total_sites() == 1
        site = report.sites[0]
        assert site.function_name == "test_cors"
        assert site.lift_status == "refused"
        assert any(h.category == "not_a_classifier" for h in site.hazards), (
            f"expected a not_a_classifier hazard, got {[h.category for h in site.hazards]}"
        )

    def test_analyzer_demotes_setup_method(self, tmp_path):
        _write(
            tmp_path / "module" / "thing_test.py",
            "def setUp():\n    if 'a' in 'b': return 'x'\n    return 'y'\n",
        )
        report = analyze(tmp_path)
        assert report.total_sites() == 1
        site = report.sites[0]
        assert site.lift_status == "refused"
        assert any(h.category == "not_a_classifier" for h in site.hazards)

    def test_analyzer_demotes_conftest_site(self, tmp_path):
        _write(
            tmp_path / "conftest.py",
            "def my_fixture(x):\n    if 'a' in x: return 'alpha'\n    return 'beta'\n",
        )
        report = analyze(tmp_path)
        assert report.total_sites() == 1
        site = report.sites[0]
        assert site.lift_status == "refused"
        assert any(h.category == "not_a_classifier" for h in site.hazards)

    def test_analyzer_demotes_unittest_fixture_in_test_dir(self, tmp_path):
        # Combination: name pattern + path pattern. Should still be one
        # not_a_classifier hazard (no double-counting).
        _write(
            tmp_path / "tests" / "test_x.py",
            "def tearDownClass():\n    if 'a' in 'b': return 'x'\n    return 'y'\n",
        )
        report = analyze(tmp_path)
        assert report.total_sites() == 1
        site = report.sites[0]
        assert site.lift_status == "refused"

    def test_real_classifier_in_non_test_path_is_unaffected(self, tmp_path):
        """Sanity check: real production code keeps its lift_status."""
        _write(
            tmp_path / "src" / "triage.py",
            "def triage(ticket):\n    if 'crash' in ticket: return 'bug'\n    return 'feature'\n",
        )
        report = analyze(tmp_path)
        assert report.total_sites() == 1
        site = report.sites[0]
        assert site.lift_status == "auto_liftable"
        assert site.hazards == []


# ---------------------------------------------------------------------------
# Self-host fixes (P0/P1 follow-up to the 2026-04-28 dogfood report).
#
# The analyzer used to recommend re-graduating code that was already
# wrapped (decorator or Switch subclass), recursing into its own
# generated companion modules, and double-counting every site through
# nested git worktrees. These tests pin the fixes.
# ---------------------------------------------------------------------------


_DECORATOR_RULE = """\
from postrule import ml_switch

@ml_switch(
    ml_kind='triage',
    labels={'bug', 'feature', 'question'},
)
def my_rule(ticket):
    if ticket['kind'] == 'crash':
        return 'bug'
    if ticket['kind'] == 'request':
        return 'feature'
    return 'question'
"""


_SWITCH_SUBCLASS_RULE = """\
from postrule import Switch

class TicketRouter(Switch):
    labels = ('bug', 'feature', 'question')

    def _evidence_severity(self, ticket):
        return ticket.get('severity', 'low')

    def _rule(self, evidence):
        if evidence.severity == 'high':
            return 'bug'
        if evidence.severity == 'medium':
            return 'feature'
        return 'question'
"""


_PLAIN_RULE = """\
def my_rule(ticket):
    if ticket['kind'] == 'crash':
        return 'bug'
    if ticket['kind'] == 'request':
        return 'feature'
    return 'question'
"""


class TestSelfHostDecoratorSkip:
    def test_decorator_wrapped_function_is_already_dendrified(self, tmp_path):
        _write(tmp_path / "src" / "rules.py", _DECORATOR_RULE)
        report = analyze(tmp_path)
        assert report.total_sites() == 0
        assert report.already_dendrified_count() == 1
        fp, fn, line = report.already_dendrified[0]
        assert fp.endswith("rules.py")
        assert fn == "my_rule"
        assert line >= 1

    def test_dotted_decorator_alias_is_recognized(self, tmp_path):
        _write(
            tmp_path / "src" / "rules.py",
            (
                "import postrule\n\n"
                "@postrule.ml_switch(ml_kind='x', labels={'a', 'b'})\n"
                "def my_rule(t):\n"
                "    if t == 'a': return 'a'\n"
                "    return 'b'\n"
            ),
        )
        report = analyze(tmp_path)
        assert report.total_sites() == 0
        assert report.already_dendrified_count() == 1

    def test_undecorated_peer_still_emits_a_site(self, tmp_path):
        _write(tmp_path / "src" / "rules.py", _PLAIN_RULE)
        report = analyze(tmp_path)
        assert report.total_sites() == 1
        assert report.already_dendrified_count() == 0
        assert report.sites[0].function_name == "my_rule"


class TestSelfHostSwitchSubclassSkip:
    def test_switch_subclass_methods_are_skipped(self, tmp_path):
        _write(tmp_path / "src" / "router.py", _SWITCH_SUBCLASS_RULE)
        report = analyze(tmp_path)
        # Both _rule and _evidence_severity should be skipped.
        assert report.total_sites() == 0

    def test_dotted_base_postrule_switch_is_recognized(self, tmp_path):
        _write(
            tmp_path / "src" / "router.py",
            (
                "import postrule\n\n"
                "class TicketRouter(postrule.Switch):\n"
                "    def _rule(self, evidence):\n"
                "        if evidence.x == 'a': return 'a'\n"
                "        return 'b'\n"
            ),
        )
        report = analyze(tmp_path)
        assert report.total_sites() == 0

    def test_non_switch_subclass_still_analyzes_methods(self, tmp_path):
        _write(
            tmp_path / "src" / "other.py",
            (
                "class MyClassifier:\n"
                "    def classify(self, ticket):\n"
                "        if ticket == 'a': return 'a'\n"
                "        if ticket == 'b': return 'b'\n"
                "        return 'c'\n"
            ),
        )
        report = analyze(tmp_path)
        assert report.total_sites() == 1
        assert report.sites[0].function_name == "classify"


class TestSelfHostGeneratedDirIgnored:
    def test_postrule_generated_directory_is_skipped(self, tmp_path):
        _write(tmp_path / "src" / "rules.py", _PLAIN_RULE)
        # Synthesize a generated companion module that the analyzer
        # used to recurse into and re-suggest for graduation.
        _write(
            tmp_path / "src" / "__postrule_generated__" / "triage_switch.py",
            (
                "from postrule import Switch\n"
                "class TriageSwitch(Switch):\n"
                "    def _rule(self, evidence):\n"
                "        if evidence.x == 'a': return 'a'\n"
                "        return 'b'\n"
            ),
        )
        report = analyze(tmp_path)
        # Only the user's plain rule should be reported.
        assert report.total_sites() == 1
        assert report.sites[0].file_path.endswith("rules.py")
        assert "__postrule_generated__" not in report.sites[0].file_path


class TestSelfHostProjectSelfBlacklist:
    def test_postrule_repo_self_scan_skips_src_postrule(self, tmp_path):
        # Mimic the Postrule repo layout: pyproject.toml at root with
        # name = "postrule", and a src/postrule/ tree that the analyzer
        # would otherwise flag (analyzer's own _classify_regime is
        # the canonical case).
        _write(
            tmp_path / "pyproject.toml",
            '[project]\nname = "postrule"\nversion = "1.0.0"\n',
        )
        _write(
            tmp_path / "src" / "postrule" / "library_helper.py",
            (
                "def _internal_bucket(n):\n"
                "    if n == 0: return 'unknown'\n"
                "    if n < 30: return 'narrow'\n"
                "    return 'high'\n"
            ),
        )
        # User code in another tree should still be scanned.
        _write(
            tmp_path / "examples" / "x.py",
            ("def my_rule(t):\n    if t == 'a': return 'a'\n    return 'b'\n"),
        )
        report = analyze(tmp_path)
        assert report.total_sites() == 1
        assert report.sites[0].file_path.startswith("examples/")

    def test_postrule_named_package_variant_also_skips(self, tmp_path):
        _write(
            tmp_path / "pyproject.toml",
            '[project]\nname = "postrule-server"\nversion = "1.0.0"\n',
        )
        _write(
            tmp_path / "src" / "postrule" / "x.py",
            "def f(t):\n    if t == 'a': return 'a'\n    return 'b'\n",
        )
        report = analyze(tmp_path)
        assert report.total_sites() == 0

    def test_unrelated_project_is_not_blacklisted(self, tmp_path):
        # Different project name → src/<name>/ is scanned normally.
        _write(
            tmp_path / "pyproject.toml",
            '[project]\nname = "their_app"\nversion = "0.1.0"\n',
        )
        _write(
            tmp_path / "src" / "postrule" / "x.py",
            "def f(t):\n    if t == 'a': return 'a'\n    return 'b'\n",
        )
        report = analyze(tmp_path)
        assert report.total_sites() == 1


class TestSelfHostNestedWorktreeSkip:
    def test_nested_git_worktree_is_skipped(self, tmp_path):
        _write(tmp_path / "src" / "rules.py", _PLAIN_RULE)
        # A git worktree marks itself with a `.git` *file* at its root
        # pointing back to the main gitdir. Mirror the user's tree
        # under .claude/worktrees/ so the file globs would otherwise
        # double-count it.
        worktree_root = tmp_path / "subdir" / "wt"
        _write(worktree_root / ".git", "gitdir: /tmp/somewhere/.git/worktrees/wt\n")
        _write(worktree_root / "src" / "rules.py", _PLAIN_RULE)
        report = analyze(tmp_path)
        assert report.total_sites() == 1
        assert report.sites[0].file_path.startswith("src/")


class TestAnalyzeFunctionSourceAlreadyDendrified:
    def test_decorated_top_level_function_returns_already_dendrified(self):
        from postrule.analyzer import analyze_function_source

        result = analyze_function_source(_DECORATOR_RULE, "my_rule")
        assert result.lift_status.value == "already_dendrified"
        assert result.hazards == []


# ---------------------------------------------------------------------------
# P4 widening — dict-driven argmax scanners (P1 #5 from the dogfood report).
#
# ReferenceRule.classify is the canonical instance: scores each label by
# token-set intersection over a per-label dict and returns the highest-
# scoring label. Returns are dynamic (return best_label), not literal
# strings, so the original P1-P6 detectors all missed it.
# ---------------------------------------------------------------------------


class TestPatternP4DictArgmax:
    def test_dict_items_argmax_is_detected(self, tmp_path):
        _write(
            tmp_path / "rules.py",
            (
                "def classify(self, text):\n"
                "    tokens = set(text.split())\n"
                "    best_label = self.fallback_label\n"
                "    best_score = 0\n"
                "    for label, kws in self.keywords_per_label.items():\n"
                "        score = len(tokens & kws)\n"
                "        if score > best_score:\n"
                "            best_score = score\n"
                "            best_label = label\n"
                "    return best_label\n"
            ),
        )
        report = analyze(tmp_path)
        assert report.total_sites() == 1
        site = report.sites[0]
        assert site.function_name == "classify"
        assert site.pattern == "P4"

    def test_unrelated_dict_iteration_is_not_a_match(self, tmp_path):
        # Loop iterates a mapping but the function returns a value
        # unrelated to the loop variable. Should NOT match.
        _write(
            tmp_path / "fn.py",
            (
                "def total(d):\n"
                "    s = 0\n"
                "    for k, v in d.items():\n"
                "        s += v\n"
                "    return s\n"
            ),
        )
        report = analyze(tmp_path)
        assert report.total_sites() == 0

    def test_existing_keyword_scanner_still_matches(self, tmp_path):
        # The classic `if kw in text: return LABEL` shape was already
        # detected (typically as P1 since detector order favors P1
        # over P4). Pin that the new dict-argmax detector hasn't
        # broken it.
        _write(
            tmp_path / "fn.py",
            (
                "def route(text):\n"
                "    if 'urgent' in text: return 'p0'\n"
                "    if 'bug' in text: return 'bug'\n"
                "    return 'other'\n"
            ),
        )
        report = analyze(tmp_path)
        assert report.total_sites() == 1
        assert report.sites[0].pattern in ("P1", "P4")
