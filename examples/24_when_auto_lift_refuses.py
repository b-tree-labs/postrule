# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""When `dendra init --auto-lift` refuses, and how to fix the source.

Phase 5 of Dendra's analyzer is prescriptive: for each candidate site,
it tells you what's blocking auto-lifting and the minimum diff that
would unblock it. This script walks five categories of refusal so you
can see the diagnostics before they show up in your own analyzer
output.

The five categories: not-a-classifier, eval/exec, dynamic dispatch,
side-effect-bearing evidence, and multi-arg without annotations.

Run:
    python examples/24_when_auto_lift_refuses.py
"""

from __future__ import annotations

import textwrap

from dendra.analyzer import analyze_function_source

CASES = [
    (
        "not_a_classifier",
        textwrap.dedent(
            """\
            def test_cors():
                client = TestClient(app)
                response = client.options("/")
                assert response.status_code == 200
                assert response.text == "OK"
            """
        ),
        "test_cors",
    ),
    (
        "eval_exec",
        textwrap.dedent(
            """\
            def evaluate(expr: str) -> str:
                if eval(expr):
                    return "true"
                return "false"
            """
        ),
        "evaluate",
    ),
    (
        "dynamic_dispatch",
        textwrap.dedent(
            """\
            def route(self, text: str, kind: str) -> str:
                handler = getattr(self, "handle_" + kind)
                if handler.priority > 5:
                    return "high"
                return "low"
            """
        ),
        "route",
    ),
    (
        "side_effect_evidence",
        textwrap.dedent(
            """\
            def maybe_charge(req):
                response = api.charge(req)
                if response.ok:
                    return "charged"
                return "skipped"
            """
        ),
        "maybe_charge",
    ),
    (
        "multi_arg_no_annotation",
        textwrap.dedent(
            """\
            def route_request(method, path, headers):
                if method == "POST" and path.startswith("/api"):
                    return "api"
                return "ui"
            """
        ),
        "route_request",
    ),
]


def main() -> None:
    for category, source, fn_name in CASES:
        print(f"\n{'=' * 70}")
        print(f"Category: {category}")
        print(f"Function: {fn_name}")
        print(f"{'=' * 70}")
        print("Source:")
        for line in source.rstrip().split("\n"):
            print(f"  {line}")
        print()

        result = analyze_function_source(source, fn_name)
        print(f"Lift status: {result.lift_status.value}")
        for h in result.hazards:
            print(f"\n  [{h.severity}] {h.category} at line {h.line}")
            print(f"      Reason: {h.reason}")
            print(f"      Fix:    {h.suggested_fix}")

    print(f"\n{'=' * 70}")
    print("Each refusal points at a specific line + a specific fix.")
    print("Run `dendra analyze --suggest-refactors` on your repo for the")
    print("same diagnostics across every candidate site.")


if __name__ == "__main__":
    main()
