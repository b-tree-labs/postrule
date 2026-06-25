# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Determinism classifier: deterministic (exact) sites are recommended to keep
as a rule, not wrapped; fuzzy/free-text sites stay graduation candidates."""

from __future__ import annotations

import ast

from postrule.analyzer import (
    _classify_determinism,
    _compute_priority_score,
    analyze,
    render_text,
)


def _fn(src: str) -> ast.FunctionDef:
    return next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef))


class TestClassifyDeterminism:
    def test_keyword_substring_scan_is_fuzzy(self):
        fn = _fn('def f(t):\n    if "crash" in t.lower():\n        return "bug"\n    return "x"')
        assert _classify_determinism(fn) == "fuzzy"

    def test_regex_is_fuzzy(self):
        fn = _fn(
            'import re\ndef f(t):\n    if re.search("x", t):\n        return "a"\n    return "b"'
        )
        assert _classify_determinism(fn) == "fuzzy"

    def test_exact_equality_is_deterministic(self):
        fn = _fn('def f(c):\n    if c == 200:\n        return "ok"\n    return "err"')
        assert _classify_determinism(fn) == "deterministic"

    def test_membership_in_literal_set_is_deterministic(self):
        fn = _fn('def f(c):\n    if c in {404, 410}:\n        return "missing"\n    return "ok"')
        assert _classify_determinism(fn) == "deterministic"

    def test_literal_dict_lookup_is_deterministic(self):
        fn = _fn('def f(c):\n    t = {"a": "x", "b": "y"}\n    return t[c]')
        assert _classify_determinism(fn) == "deterministic"

    def test_fuzzy_takes_precedence_over_exact(self):
        # mixes an exact check with a free-text substring scan -> still fuzzy
        fn = _fn(
            "def f(c, t):\n"
            '    if c == 0:\n        return "z"\n'
            '    if "err" in t.lower():\n        return "bug"\n'
            '    return "x"'
        )
        assert _classify_determinism(fn) == "fuzzy"


class TestDeterminismScoring:
    def test_deterministic_is_downranked_near_zero(self):
        det = _compute_priority_score(5.0, "hot", "auto_liftable", "deterministic")
        fuzzy = _compute_priority_score(5.0, "hot", "auto_liftable", "fuzzy")
        assert det < 1.0 < fuzzy
        # unknown stays neutral (no haircut) — preserves prior baselines
        assert _compute_priority_score(5.0, "hot", "auto_liftable", "unknown") == fuzzy


class TestKeepAsRuleVerdict:
    def test_deterministic_site_kept_fuzzy_graduates(self, tmp_path):
        (tmp_path / "sites.py").write_text(
            "def triage(t):\n"
            '    t = (t or "").lower()\n'
            '    if "crash" in t or "error" in t:\n        return "bug"\n'
            '    if "refund" in t:\n        return "billing"\n'
            '    return "other"\n\n'
            "def lookup(code):\n"
            '    table = {"a": "open", "b": "closed", "c": "pending"}\n'
            "    return table[code]\n"
        )
        report = analyze(tmp_path)
        by_name = {s.function_name: s for s in report.sites}
        assert by_name["lookup"].recommendation == "keep_as_rule"
        assert by_name["lookup"].determinism == "deterministic"
        assert by_name["triage"].recommendation == "graduate"
        # rendered text surfaces the keep-as-rule section, not in the wrap table
        out = render_text(report)
        assert "Keep as rule — already deterministic" in out
        assert "lookup" in out.split("Keep as rule")[1]
