# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Phase 5: prescriptive analyzer hazard detection.

The analyzer doesn't just score sites by fit; it also tells the user
*what's blocking* a site from auto-lifting and *what minimum diff* would
unblock it. Each ClassificationSite carries a ``hazards`` list and a
``lift_status`` (auto_liftable | needs_annotation | refused).

The detection logic delegates to the lifters (``postrule.lifters.*``)
when available so the analyzer's diagnostics line up with the lifters'
actual refusal reasons. The analyzer never silently disagrees with the
lifter.
"""

from __future__ import annotations

import textwrap

from postrule.analyzer import Hazard, LiftStatus, analyze_function_source


def _src(s: str) -> str:
    """Convenience for inline test sources."""
    return textwrap.dedent(s).lstrip()


# ----------------------------------------------------------------------
# Each test states one (function shape, expected lift status, expected
# hazards) triple.
# ----------------------------------------------------------------------


class TestAutoLiftableShapes:
    """Pure classifiers with side-effect-free branches lift cleanly."""

    def test_simple_if_elif_with_pure_returns_is_auto_liftable(self):
        src = _src(
            """
            def triage(text: str) -> str:
                if "bug" in text:
                    return "bug"
                if "feature" in text:
                    return "feature"
                return "other"
            """
        )
        result = analyze_function_source(src, "triage")
        assert result.lift_status is LiftStatus.AUTO_LIFTABLE
        assert result.hazards == []

    def test_branch_body_with_clean_side_effect_calls_is_auto_liftable(self):
        src = _src(
            """
            def route(text: str) -> str:
                if "refund" in text:
                    log_refund(text)
                    return "refund"
                if "reset" in text:
                    log_security(text)
                    return "security"
                return "general"
            """
        )
        result = analyze_function_source(src, "route")
        assert result.lift_status is LiftStatus.AUTO_LIFTABLE


class TestSideEffectBearingEvidence:
    """A function that BINDS a side-effect call's result to a name then
    branches on it has hazardous evidence — refused unless annotated."""

    def test_charge_then_branch_is_refused_with_specific_diagnostic(self):
        src = _src(
            """
            def maybe_charge(req):
                response = api.charge(req)
                if response.ok:
                    return "charged"
                return "skipped"
            """
        )
        result = analyze_function_source(src, "maybe_charge")
        assert result.lift_status is LiftStatus.REFUSED
        # At least one hazard must point at the side-effect category
        # and reference the offending line.
        hazards = [h for h in result.hazards if h.category == "side_effect_evidence"]
        assert hazards, f"expected side_effect_evidence hazard, got {result.hazards}"
        assert any("api.charge" in h.reason for h in hazards)
        assert any(h.suggested_fix for h in hazards)


class TestDynamicDispatch:
    """getattr / eval / exec block static evidence detection."""

    def test_getattr_blocks_lifting(self):
        src = _src(
            """
            def route(self, text, kind):
                handler = getattr(self, "handle_" + kind)
                if handler.priority > 5:
                    return "high"
                return "low"
            """
        )
        result = analyze_function_source(src, "route")
        assert result.lift_status is LiftStatus.REFUSED
        assert any(h.category == "dynamic_dispatch" for h in result.hazards)

    def test_eval_blocks_lifting(self):
        src = _src(
            """
            def evaluate(expr: str) -> str:
                if eval(expr):
                    return "true"
                return "false"
            """
        )
        result = analyze_function_source(src, "evaluate")
        assert result.lift_status is LiftStatus.REFUSED
        assert any(h.category == "eval_exec" for h in result.hazards)


class TestZeroArgNoReturn:
    """Pytest-style functions are not classifiers."""

    def test_zero_arg_no_return_refused(self):
        src = _src(
            """
            def test_cors():
                client = TestClient(app)
                response = client.options("/")
                assert response.status_code == 200
                assert response.text == "OK"
            """
        )
        result = analyze_function_source(src, "test_cors")
        assert result.lift_status is LiftStatus.REFUSED
        assert any(h.category == "not_a_classifier" for h in result.hazards)


class TestHazardSuggestionFormat:
    """Each hazard must carry enough info for a user to act on it."""

    def test_hazard_fields_present(self):
        src = _src(
            """
            def maybe_charge(req):
                response = api.charge(req)
                if response.ok:
                    return "charged"
                return "skipped"
            """
        )
        result = analyze_function_source(src, "maybe_charge")
        for h in result.hazards:
            assert isinstance(h, Hazard)
            assert h.category  # non-empty string
            assert h.reason  # non-empty string
            assert h.suggested_fix  # non-empty string
            assert h.line >= 1


class TestNeedsAnnotationStatus:
    """Multi-arg functions without type hints are liftable IF the user
    annotates evidence inputs explicitly. Surface this as needs_annotation,
    not refused."""

    def test_multi_arg_without_annotations_needs_annotation(self):
        src = _src(
            """
            def route_request(method, path, headers):
                if method == "POST" and path.startswith("/api"):
                    return "api"
                return "ui"
            """
        )
        result = analyze_function_source(src, "route_request")
        assert result.lift_status is LiftStatus.NEEDS_ANNOTATION
        assert any(h.category == "multi_arg_no_annotation" for h in result.hazards)
