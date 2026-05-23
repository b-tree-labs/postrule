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

"""Static analyzer — finds classification sites in a codebase.

The analyzer walks Python source files, parses each to an AST,
and applies a pattern library to identify functions that look
like classification decision points. Each match is scored for
**wrap priority** — a composite of graduation-fitness (regime,
labels, pattern), volume estimate (cold/warm/hot from AST
signals), and lift status (auto-liftable / needs-annotation /
refused) — so operators can prioritize sites to graduate.

Six patterns ship in v1:

- **P1** if-elif-else with string returns
- **P2** match-case string dispatcher
- **P3** dict-lookup dispatcher
- **P4** keyword-scanner
- **P5** regex dispatcher
- **P6** model-prompted classifier

Zero external dependencies. Runs on any repo via
``postrule analyze <path>``.
"""

from __future__ import annotations

import ast
import enum as _enum
import json
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Internal-switch wrapping (Postrule-on-Postrule dogfood). Direct imports
# from sub-modules — going through ``postrule.__init__`` would create a
# circular import since the package init imports analyzer.
from postrule.core import Phase
from postrule.decorator import ml_switch

# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class ClassificationSite:
    """One identified classification decision point."""

    file_path: str
    function_name: str
    line_start: int
    line_end: int
    pattern: str  # one of "P1".."P6"
    labels: list[str] = field(default_factory=list)
    label_cardinality: int = 0
    regime: str = "unknown"  # "narrow" | "medium" | "high" | "unknown"
    # Volume estimate from AST signals (route decorators, file-path
    # heuristics). One of "cold" | "warm" | "hot". Cohort signal in
    # v1.1 will refine this from real call counts.
    volume_estimate: str = "warm"
    # Composite priority score (0-5). Combines graduation-fitness,
    # volume estimate, and lift status. See _compute_priority_score.
    priority_score: float = 0.0
    # Phase 5 prescriptive output: hazard list + lift status. Empty
    # hazards + AUTO_LIFTABLE means `postrule init --auto-lift` would
    # succeed cleanly. Populated by analyze() when run with hazard
    # detection enabled.
    hazards: list[Hazard] = field(default_factory=list)
    lift_status: str = "auto_liftable"  # str for json-serializable round-trip


@dataclass
class AnalyzerReport:
    """Complete analyzer output for a codebase scan."""

    root: str
    files_scanned: int
    sites: list[ClassificationSite] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # Functions skipped because they're already wrapped by Postrule
    # (decorated with @ml_switch / @postrule.ml_switch) or live inside a
    # ``Switch`` subclass. Tracked so the report can show "X sites
    # already dendrified" without recommending re-graduation. Tuples
    # are (file_path, function_name, line_start).
    already_dendrified: list[tuple[str, str, int]] = field(default_factory=list)

    def by_priority_desc(self) -> list[ClassificationSite]:
        return self.sort_sites(key="priority")

    def sort_sites(self, key: str = "priority", reverse: bool = False) -> list[ClassificationSite]:
        """Return sites sorted by the given key.

        Supported keys (default ``priority``):

        - ``priority`` — composite ``priority_score`` descending; ties
          broken by ``file_path``, ``line_start``.
        - ``location`` — ``file_path`` ascending, then ``line_start``.
        - ``pattern`` — pattern name (P1..P6); ``priority_score``
          descending within each pattern.
        - ``regime`` — narrow → medium → high → unknown; priority
          descending within each regime.
        - ``lift`` — auto_liftable → needs_annotation → refused →
          already_dendrified; priority descending within each.

        ``reverse=True`` flips the final order.
        """
        if key == "priority":
            sites = sorted(
                self.sites,
                key=lambda s: (-s.priority_score, s.file_path, s.line_start),
            )
        elif key == "location":
            sites = sorted(self.sites, key=lambda s: (s.file_path, s.line_start))
        elif key == "pattern":
            sites = sorted(self.sites, key=lambda s: (s.pattern, -s.priority_score))
        elif key == "regime":
            regime_order = {"narrow": 0, "medium": 1, "high": 2, "unknown": 3}
            sites = sorted(
                self.sites,
                key=lambda s: (
                    regime_order.get(s.regime, 99),
                    -s.priority_score,
                ),
            )
        elif key == "lift":
            lift_order = {
                "auto_liftable": 0,
                "needs_annotation": 1,
                "refused": 2,
                "already_dendrified": 3,
            }
            sites = sorted(
                self.sites,
                key=lambda s: (
                    lift_order.get(s.lift_status, 99),
                    -s.priority_score,
                ),
            )
        else:
            raise ValueError(
                f"unknown sort key {key!r}; choose from priority, location, pattern, regime, lift"
            )
        return list(reversed(sites)) if reverse else sites

    def total_sites(self) -> int:
        return len(self.sites)

    def already_dendrified_count(self) -> int:
        return len(self.already_dendrified)


# ---------------------------------------------------------------------------
# Pattern detectors
# ---------------------------------------------------------------------------


def _collect_return_strings(fn: ast.FunctionDef) -> list[str]:
    """Return every string-literal value appearing in the function's returns."""
    out: list[str] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant):
            value = node.value.value
            if isinstance(value, str) and value and value not in out:
                out.append(value)
    return out


def _body_has_if_elif_string_returns(fn: ast.FunctionDef) -> bool:
    """Pattern P1 — if/elif chain whose branches each end in return <str>."""
    if not fn.body:
        return False
    # Look for at least one top-level If with a string-return branch.
    for stmt in fn.body:
        if isinstance(stmt, ast.If) and _if_branch_returns_string(stmt):
            # Require at least 2 distinct return labels for it to count.
            labels = _collect_return_strings(fn)
            return len(labels) >= 2
    return False


def _if_branch_returns_string(stmt: ast.If) -> bool:
    """Does this If (and any elif/else it chains to) include a string return?"""
    for node in ast.walk(stmt):
        if (
            isinstance(node, ast.Return)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return True
    return False


def _body_has_match_case_string_dispatch(fn: ast.FunctionDef) -> bool:
    """Pattern P2 — match/case with string returns."""
    for node in ast.walk(fn):
        if isinstance(node, ast.Match):
            for case in node.cases:
                for inner in ast.walk(case):
                    if (
                        isinstance(inner, ast.Return)
                        and isinstance(inner.value, ast.Constant)
                        and isinstance(inner.value.value, str)
                    ):
                        return True
    return False


def _body_is_dict_lookup(fn: ast.FunctionDef) -> bool:
    """Pattern P3 — function body looks like: dict[key], returning labels."""
    # Very narrow: require a local dict literal with ≥ 3 string-string entries.
    for stmt in fn.body:
        if (
            isinstance(stmt, ast.Assign)
            and isinstance(stmt.value, ast.Dict)
            and _dict_is_str_to_str(stmt.value)
        ):
            return True
    return False


def _dict_is_str_to_str(d: ast.Dict) -> bool:
    if len(d.keys) < 3:
        return False
    for k, v in zip(d.keys, d.values, strict=True):
        if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
            return False
        if not (isinstance(v, ast.Constant) and isinstance(v.value, str)):
            return False
    return True


def _dict_literal_string_values(d: ast.Dict) -> list[str]:
    """String values from a ``{str: str, ...}`` dict literal."""
    if not _dict_is_str_to_str(d):
        return []
    out: list[str] = []
    for v in d.values:
        assert isinstance(v, ast.Constant)
        value = v.value
        if isinstance(value, str) and value and value not in out:
            out.append(value)
    return out


def _collect_dict_lookup_labels(fn: ast.FunctionDef) -> list[str]:
    """Labels from P3-style ``return mapping.get(key, 'default')`` dispatchers."""
    out: list[str] = []
    for stmt in fn.body:
        if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Dict):
            for label in _dict_literal_string_values(stmt.value):
                if label not in out:
                    out.append(label)
    for node in ast.walk(fn):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        if not (isinstance(call.func, ast.Attribute) and call.func.attr == "get"):
            continue
        if len(call.args) >= 2:
            default = call.args[1]
        elif len(call.keywords) >= 1 and call.keywords[0].arg is None:
            default = call.keywords[0].value
        else:
            continue
        if isinstance(default, ast.Constant) and isinstance(default.value, str):
            label = default.value
            if label and label not in out:
                out.append(label)
    return out


def _collect_classification_labels(fn: ast.FunctionDef) -> list[str]:
    """All string labels a classifier function can emit (returns + dict maps)."""
    out = _collect_return_strings(fn)
    for label in _collect_dict_lookup_labels(fn):
        if label not in out:
            out.append(label)
    return out


def _body_has_keyword_scanner(fn: ast.FunctionDef) -> bool:
    """Pattern P4 — `if some_keyword in text: return LABEL` chains."""
    hits = 0
    for node in ast.walk(fn):
        if isinstance(node, ast.If):
            # Look for Compare with In operator
            test = node.test
            if isinstance(test, ast.Compare) and test.ops and isinstance(test.ops[0], ast.In):
                # And body returns a string literal
                for stmt in node.body:
                    if (
                        isinstance(stmt, ast.Return)
                        and isinstance(stmt.value, ast.Constant)
                        and isinstance(stmt.value.value, str)
                    ):
                        hits += 1
                        break
    return hits >= 2


def _body_has_dict_argmax_scanner(fn: ast.FunctionDef) -> bool:
    """Pattern P4 (extended) — argmax over a per-label scoring loop.

    Shape: ``for <label>, <kws> in <mapping>.items(): ... <tracker> = <label>``,
    followed by ``return <tracker>``. The canonical instance is
    :meth:`postrule.benchmarks.rules.ReferenceRule.classify`, which scores
    each label by token-set intersection and returns the highest-scoring
    one. Without this detector the analyzer misses real per-label
    scoring rules whose returns are dynamic (``return best_label``)
    rather than literal-string, surfaced as a false negative in the
    2026-04-28 dogfood report.
    """
    assigned_from_loop_var: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.For):
            continue
        iter_node = node.iter
        if not (
            isinstance(iter_node, ast.Call)
            and isinstance(iter_node.func, ast.Attribute)
            and iter_node.func.attr == "items"
        ):
            continue
        target = node.target
        if not (
            isinstance(target, ast.Tuple)
            and len(target.elts) == 2
            and all(isinstance(t, ast.Name) for t in target.elts)
        ):
            continue
        label_var = target.elts[0].id  # type: ignore[union-attr]
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Assign)
                and isinstance(inner.value, ast.Name)
                and inner.value.id == label_var
                and len(inner.targets) == 1
                and isinstance(inner.targets[0], ast.Name)
            ):
                assigned_from_loop_var.add(inner.targets[0].id)

    if not assigned_from_loop_var:
        return False

    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Return)
            and isinstance(node.value, ast.Name)
            and node.value.id in assigned_from_loop_var
        ):
            return True
    return False


def _body_uses_regex_dispatch(fn: ast.FunctionDef) -> bool:
    """Pattern P5 — re.match / re.search calls with string-literal returns."""
    uses_re = False
    has_str_return = False
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "re"
                and func.attr in ("match", "search", "fullmatch", "findall")
            ):
                uses_re = True
        if (
            isinstance(node, ast.Return)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            has_str_return = True
    return uses_re and has_str_return


def _body_is_model_prompted(fn: ast.FunctionDef) -> bool:
    """Pattern P6 — calls a language-model client and returns a string.

    Heuristic — we look for attribute accesses to known client names
    (openai.chat, anthropic.messages, requests.post, httpx.post).
    """
    model_markers = {
        "chat",
        "completions",
        "messages",
        "generate",
    }
    has_llm_call = False
    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute) and node.attr in model_markers:
            has_llm_call = True
            break
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in ("classify", "predict"):
                has_llm_call = True
                break
    if not has_llm_call:
        return False
    # Require at least one string return OR a return at all.
    return any(isinstance(n, ast.Return) for n in ast.walk(fn))


_PATTERNS: list[tuple[str, Any]] = [
    ("P1", _body_has_if_elif_string_returns),
    ("P2", _body_has_match_case_string_dispatch),
    ("P3", _body_is_dict_lookup),
    ("P4", _body_has_keyword_scanner),
    ("P4", _body_has_dict_argmax_scanner),
    ("P5", _body_uses_regex_dispatch),
    ("P6", _body_is_model_prompted),
]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _classify_regime(cardinality: int) -> str:
    """Bucket a classification site by label cardinality.

    Aligned with paper §6 (category taxonomy):
    - ``narrow`` (Regime A in the paper): cardinality < 30. Rule is a
      usable day-zero baseline; graduation by ~250 outcomes per the
      transition-curve analysis.
    - ``medium``: cardinality 30..60. Between Regime A and B. Rule is
      borderline-usable; graduation timeline depends on verdict rate.
    - ``high`` (Regime B in the paper): cardinality > 60. Rule is
      symbolic; production teams typically start at Phase 2 with a
      zero-shot LLM and accumulate outcome data via Postrule's logging
      substrate.
    - ``unknown``: cardinality 0 (analyzer could not extract labels).
    """
    if cardinality == 0:
        return "unknown"
    if cardinality < 30:
        return "narrow"
    if cardinality <= 60:
        return "medium"
    return "high"


def _compute_gate_fit(labels: list[str], pattern: str) -> float:
    """Graduation-fitness — 0-5 heuristic measuring how well-shaped
    a site is for the McNemar gate.

    - 2 base points for any matched pattern.
    - +1 for having 2-30 labels (the Regime A sweet spot per paper §6).
    - +1 for narrow/medium regime.
    - +1 for pattern types with strong outcome observability
      (P1/P4 triage-like patterns score higher).

    Internal-only — exposed to users via the composite ``priority_score``
    on ``ClassificationSite`` (see :func:`_compute_priority_score`).
    """
    score = 2.0
    n = len(labels)
    if 2 <= n < 30:
        score += 1.0
    regime = _classify_regime(n)
    if regime in ("narrow", "medium"):
        score += 1.0
    if pattern in ("P1", "P4"):
        score += 1.0
    return min(5.0, score)


# Volume estimation — coarse cold / warm / hot bucket from static AST
# signals only. Runtime measurement (cohort signal in v1.1) will refine.
# These names are matched against the rightmost dotted attribute of a
# decorator (e.g. ``@app.post`` → "post", ``@router.get`` → "get") or
# the bare name (``@route``).
_HOT_ROUTE_DECORATORS = frozenset(
    {
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "head",
        "options",
        "route",
        "api_route",
        "websocket",
        "page",
        "endpoint",
        "method",
        "view",
    }
)

# Substrings inside the (POSIX) file path that indicate a likely-cold
# call site: CLI entrypoints, schema migrations, one-off scripts.
_COLD_FILE_PATTERNS = ("cli.py", "__main__.py", "/migrations/", "/scripts/")

# Multipliers used by the priority composite. Tuned for "fit-5 hot
# auto-liftable" → 5.0 max; "fit-5 cold refused" → 0.6 (clearly
# deprioritized but not zero, because the site shape is still good).
_VOLUME_FACTOR = {"cold": 0.4, "warm": 0.7, "hot": 1.0}
_LIFT_FACTOR = {
    "refused": 0.3,
    "needs_annotation": 0.7,
    "auto_liftable": 1.0,
    "already_dendrified": 0.0,
}


def _compute_volume_estimate(node: ast.FunctionDef, file_path: str) -> str:
    """Return one of ``"cold"`` / ``"warm"`` / ``"hot"``.

    Heuristic only — examines the function's decorators (web-route
    decorators → hot) and the containing file path (cli.py /
    migrations / scripts → cold). Defaults to warm when no signal.
    """
    for dec in node.decorator_list:
        inner = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(inner, ast.Attribute) and inner.attr in _HOT_ROUTE_DECORATORS:
            return "hot"
        if isinstance(inner, ast.Name) and inner.id in _HOT_ROUTE_DECORATORS:
            return "hot"
    fp = file_path.replace("\\", "/")
    for pat in _COLD_FILE_PATTERNS:
        if pat in fp:
            return "cold"
    return "warm"


def _compute_priority_score(gate_fit: float, volume_estimate: str, lift_status: str) -> float:
    """Composite ``priority_score`` (0-5) shown to users.

    Multiplies the graduation-fitness heuristic by volume + lift
    factors so the displayed number reflects "what should I wrap
    first?", not just "is this site shaped right?".
    """
    return round(
        gate_fit * _VOLUME_FACTOR.get(volume_estimate, 0.7) * _LIFT_FACTOR.get(lift_status, 1.0),
        2,
    )


# ---------------------------------------------------------------------------
# Postrule-on-Postrule: two analyzer decisions wrapped with @ml_switch
#
# These are the analyzer's own classification decisions, exposed as
# Postrule switches so the company instance dogfoods its own primitive.
# Both stay at Phase.RULE so behavior is bit-for-bit identical to the
# pre-wrap inline code; the wrap accumulates audit-chain outcomes that
# (a) make these visible to ``_has_ml_switch_decorator`` detection
# when scanning the postrule repo, and (b) populate the cohort signal
# from B-Tree Labs's own deployment from day-1.
#
# Storage defaults to BoundedInMemoryStorage (process-local, capped),
# so a fresh ``pip install postrule`` doesn't write to disk on import.
# Hypothesis files at ``postrule/hypotheses/_classify_pattern.md`` and
# ``postrule/hypotheses/_classify_lift_status.md`` are pre-committed.
# ---------------------------------------------------------------------------


_PATTERN_LABELS = ["P1", "P2", "P3", "P4", "P5", "P6", "no_match"]
_LIFT_STATUS_LABELS = ["refused", "needs_annotation", "auto_liftable"]


@ml_switch(
    labels=_PATTERN_LABELS,
    author="@b-tree-labs:postrule-internal",
    name="_classify_pattern",
    starting_phase=Phase.RULE,
)
def _classify_pattern(node: ast.AST) -> str:
    """Dispatch over the registered pattern detectors.

    Returns the matched pattern name (``"P1"``..``"P6"``) or
    ``"no_match"`` when no detector recognises the function. Detectors
    that raise are silently skipped — a buggy detector must not abort
    the whole analyze run.
    """
    for pattern_name, detector in _PATTERNS:
        try:
            if detector(node):
                return pattern_name
        except Exception:  # noqa: BLE001 — detector failures must not kill the run
            continue
    return "no_match"


@ml_switch(
    labels=_LIFT_STATUS_LABELS,
    author="@b-tree-labs:postrule-internal",
    name="_classify_lift_status",
    starting_phase=Phase.RULE,
)
def _classify_lift_status(hazards: list[Hazard]) -> str:
    """Verdict from the hazard list.

    - Any error-severity hazard → ``"refused"``.
    - Otherwise any hazard → ``"needs_annotation"``.
    - Empty hazards → ``"auto_liftable"``.
    """
    if any(h.severity == "error" for h in hazards):
        return "refused"
    if hazards:
        return "needs_annotation"
    return "auto_liftable"


# ---------------------------------------------------------------------------
# Test-site detection (mirrors scripts/enrich_landing_corpus.py)
#
# Sites in test directories or with pytest/unittest naming conventions are
# demoted with a ``not_a_classifier`` hazard so users running ``postrule
# analyze`` on their own repo don't see test fixtures dominate the top of
# the fit list.
# ---------------------------------------------------------------------------


_TEST_PATH_FRAGMENTS: tuple[str, ...] = (
    "/tests/",
    "/test/",
    "_test.py",
    "/conftest.py",
)
_UNITTEST_FIXTURE_NAMES: frozenset[str] = frozenset(
    {
        "setUp",
        "tearDown",
        "setUpClass",
        "tearDownClass",
        "setUpModule",
        "tearDownModule",
    }
)


def _is_test_site(
    file_path: str,
    function_name: str,
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """True if this site looks like a test fixture or test function.

    Heuristics, any of which trips the demotion:

    - File path contains ``/tests/``, ``/test/``, ``_test.py``, or
      ``/conftest.py``.
    - Function name matches ``test_<...>``.
    - Function name is a unittest fixture (setUp/tearDown family).
    - Function takes zero args AND has no ``return`` statements (covers
      pytest-style sanity tests that the analyzer's pattern detectors
      occasionally pick up).
    """
    # Normalize so a top-level "tests/" matches "/tests/".
    normalized = "/" + file_path.replace("\\", "/").lstrip("/")
    if any(frag in normalized for frag in _TEST_PATH_FRAGMENTS):
        return True
    if function_name.startswith("test_"):
        return True
    if function_name in _UNITTEST_FIXTURE_NAMES:
        return True

    args = fn.args
    total_args = (
        len(args.posonlyargs)
        + len(args.args)
        + len(args.kwonlyargs)
        + (1 if args.vararg else 0)
        + (1 if args.kwarg else 0)
    )
    return total_args == 0 and not any(isinstance(node, ast.Return) for node in ast.walk(fn))


# ---------------------------------------------------------------------------
# Per-file analysis
# ---------------------------------------------------------------------------


def _has_ml_switch_decorator(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if ``fn`` is decorated with ``@ml_switch`` / ``@postrule.ml_switch``.

    Matches both the bare form (``@ml_switch``) and the call form
    (``@ml_switch(labels=...)``), under either the bare-import alias or
    the dotted-attribute form. Intentionally syntactic: the analyzer
    walks ASTs without resolving imports, so a user who aliases the
    decorator under a different name will read as un-wrapped here.
    That matches the semantics the lifters use and is documented as a
    known shape in the FAQ.
    """
    for dec in fn.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name) and target.id == "ml_switch":
            return True
        if isinstance(target, ast.Attribute) and target.attr == "ml_switch":
            return True
    return False


def _collect_switch_subclass_method_lines(tree: ast.Module) -> set[int]:
    """Return ``lineno`` of every method on a class that subclasses ``Switch``.

    The check is syntactic — a base named ``Switch`` (bare or dotted).
    Methods of these classes are skipped entirely because the analyzer
    cannot tell ``_rule`` apart from ``_evidence_*`` / ``_when_*`` /
    ``_on_*`` helpers without semantic understanding of the
    :class:`postrule.Switch` contract, and reporting any of them as
    classification sites is a false positive (see the 2026-04-28
    dogfood report for the smoking-gun cases).
    """
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        is_switch_subclass = any(
            (isinstance(b, ast.Name) and b.id == "Switch")
            or (isinstance(b, ast.Attribute) and b.attr == "Switch")
            for b in node.bases
        )
        if not is_switch_subclass:
            continue
        for child in ast.walk(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                lines.add(child.lineno)
    return lines


def _analyze_file(
    path: Path, root: Path
) -> tuple[list[ClassificationSite], list[str], list[tuple[str, str, int]]]:
    sites: list[ClassificationSite] = []
    errors: list[str] = []
    already_dendrified: list[tuple[str, str, int]] = []
    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as e:
        errors.append(f"{path.relative_to(root)}: read failed ({e})")
        return sites, errors, already_dendrified

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        errors.append(f"{path.relative_to(root)}: parse failed ({e.msg})")
        return sites, errors, already_dendrified

    rel_path_str = str(path.relative_to(root))
    switch_method_lines = _collect_switch_subclass_method_lines(tree)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Already-dendrified shapes — skip detection entirely. Track
        # decorator-wrapped functions so the report can show the
        # surface area; Switch-subclass methods are noisier (multiple
        # helpers per class) so they're skipped silently.
        if _has_ml_switch_decorator(node):
            already_dendrified.append((rel_path_str, node.name, node.lineno))
            continue
        if node.lineno in switch_method_lines:
            continue
        matched_pattern = _classify_pattern(node)
        if matched_pattern == "no_match":
            continue

        labels = _collect_classification_labels(node)
        # Run Phase 5 hazard detection for this site. Detector list lives
        # at module bottom; each detector returns 0+ Hazard objects.
        node_hazards: list[Hazard] = []
        for detector in _HAZARD_DETECTORS:
            try:
                node_hazards.extend(detector(node))
            except Exception:  # noqa: BLE001 - detector failures must not kill the run
                continue
        # Test-suite demotion: pytest fixtures and unittest setUp/tearDown
        # methods scored at fit 5.0 in real-codebase testing because they
        # look structurally like classifiers (if/elif chains with string
        # returns). Demote them to refused with a not_a_classifier hazard
        # so the user sees what was found and why instead of the noise
        # dominating the top of the fit list. Mirrors the existing
        # landing-corpus filter, applied at the analyzer layer.
        if _is_test_site(rel_path_str, node.name, node) and not any(
            h.category == "not_a_classifier" for h in node_hazards
        ):
            node_hazards.append(
                Hazard(
                    category="not_a_classifier",
                    line=node.lineno,
                    reason=(
                        f"Function {node.name!r} at {rel_path_str}:{node.lineno} "
                        "looks like a test fixture or test function (path or "
                        "name matches a test convention), not a production "
                        "classifier."
                    ),
                    suggested_fix=(
                        "If this really is a classifier worth wrapping, move "
                        "it out of the test path and rename it so it doesn't "
                        "match pytest/unittest conventions."
                    ),
                    severity="error",
                )
            )
        lift_status = _classify_lift_status(node_hazards)

        gate_fit = _compute_gate_fit(labels, matched_pattern)
        volume_estimate = _compute_volume_estimate(node, rel_path_str)
        site = ClassificationSite(
            file_path=rel_path_str,
            function_name=node.name,
            line_start=node.lineno,
            line_end=getattr(node, "end_lineno", node.lineno),
            pattern=matched_pattern,
            labels=labels,
            label_cardinality=len(labels),
            regime=_classify_regime(len(labels)),
            volume_estimate=volume_estimate,
            priority_score=_compute_priority_score(gate_fit, volume_estimate, lift_status),
            hazards=node_hazards,
            lift_status=lift_status,
        )
        sites.append(site)

    return sites, errors, already_dendrified


# ---------------------------------------------------------------------------
# Top-level analyzer
# ---------------------------------------------------------------------------


_DEFAULT_IGNORE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "build",
    "dist",
    ".ruff_cache",
    # Postrule's own generated companion modules — re-suggesting them for
    # graduation creates a re-graduation cycle bug as soon as
    # `postrule init --auto-lift` produces any file.
    "__postrule_generated__",
    # Agent-harness directory (Claude Code). Frequently contains nested
    # git worktrees that mirror the repo and double every site count.
    ".claude",
}


def analyze(
    root_path: str | Path,
    *,
    ignore_dirs: Iterable[str] | None = None,
) -> AnalyzerReport:
    """Walk ``root_path`` and identify classification sites."""
    root = Path(root_path).resolve()
    if not root.exists():
        return AnalyzerReport(
            root=str(root),
            files_scanned=0,
            errors=[f"path not found: {root}"],
        )

    ignore = set(_DEFAULT_IGNORE_DIRS)
    if ignore_dirs is not None:
        ignore |= set(ignore_dirs)

    # Project-self-blacklist: when scanning the Postrule repo itself,
    # skip its own src/ tree. Postrule's analyzer/gates/models adapter
    # plumbing is correctly P1/P6 in shape but is library
    # infrastructure, not customer code. The dogfood report's
    # smoking-gun self-flag is `analyzer.py:_classify_regime` — the
    # analyzer's own bucket function.
    skip_postrule_src = False
    if not root.is_file():
        pyproject = root / "pyproject.toml"
        if pyproject.is_file():
            try:
                pp_text = pyproject.read_text(encoding="utf-8", errors="replace")
                # Match either `name = "postrule"` or `name = "postrule-..."`,
                # tolerating arbitrary whitespace.
                import re as _re

                if _re.search(
                    r'^\s*name\s*=\s*"postrule(?:-[a-z0-9_-]+)?"',
                    pp_text,
                    flags=_re.MULTILINE,
                ):
                    skip_postrule_src = True
            except OSError:
                pass

    files_scanned = 0
    sites: list[ClassificationSite] = []
    errors: list[str] = []
    already_dendrified: list[tuple[str, str, int]] = []

    if root.is_file():
        scan_root = root.parent if root.parent.exists() else root
        if root.suffix == ".py":
            file_sites, file_errs, file_dendrified = _analyze_file(root, scan_root)
            sites.extend(file_sites)
            errors.extend(file_errs)
            already_dendrified.extend(file_dendrified)
            files_scanned = 1
    else:
        # Pre-compute a set of nested-worktree roots to skip. A git
        # worktree is marked by a `.git` *file* (not a directory) at
        # its root pointing back to the main repo's gitdir. Skipping
        # everything under those roots prevents the parallel-worktree
        # double-count documented in the 2026-04-28 dogfood report.
        nested_worktree_roots: set[Path] = set()
        for git_marker in root.rglob(".git"):
            if git_marker.parent == root:
                continue  # the main repo itself
            if git_marker.is_file():
                nested_worktree_roots.add(git_marker.parent)

        for py_file in sorted(root.rglob("*.py")):
            rel_parts = py_file.relative_to(root).parts
            if any(part in ignore for part in rel_parts):
                continue
            if any(worktree in py_file.parents for worktree in nested_worktree_roots):
                continue
            if (
                skip_postrule_src
                and len(rel_parts) >= 2
                and rel_parts[0] == "src"
                and rel_parts[1] == "postrule"
            ):
                continue
            files_scanned += 1
            file_sites, file_errs, file_dendrified = _analyze_file(py_file, root)
            sites.extend(file_sites)
            errors.extend(file_errs)
            already_dendrified.extend(file_dendrified)

    return AnalyzerReport(
        root=str(root),
        files_scanned=files_scanned,
        sites=sites,
        errors=errors,
        already_dendrified=already_dendrified,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_text(
    report: AnalyzerReport,
    *,
    sort_key: str = "priority",
    reverse: bool = False,
) -> str:
    lines = [
        "Postrule static analyzer — classification sites",
        "=" * 60,
        f"Root:           {report.root}",
        f"Files scanned:  {report.files_scanned:,}",
        f"Sites found:    {report.total_sites():,}",
    ]
    if report.already_dendrified:
        lines.append(
            f"Already wrapped: {report.already_dendrified_count():,} "
            f"(decorator-wrapped functions skipped)"
        )
    lines.append("")
    if report.errors:
        lines.append(f"Parse warnings ({len(report.errors)}):")
        for e in report.errors[:10]:
            lines.append(f"  - {e}")
        if len(report.errors) > 10:
            lines.append(f"  ... and {len(report.errors) - 10} more")
        lines.append("")

    if not report.sites:
        lines.append("No classification sites identified.")
        lines.append(
            "Hint: analyzer v1 ships 6 patterns (P1-P6). "
            "If you expected hits, try `postrule analyze --verbose` (not yet)."
        )
        return "\n".join(lines)

    lines.append(
        f"{'file:line':<40} {'function':<22} {'ptn':>4} {'labels':>8} "
        f"{'regime':>8} {'vol':>5} {'priority':>9}"
    )
    lines.append("-" * 102)
    for s in report.sort_sites(key=sort_key, reverse=reverse):
        file_label = f"{s.file_path}:{s.line_start}"
        lines.append(
            f"{file_label:<40} {s.function_name:<22} "
            f"{s.pattern:>4} {s.label_cardinality:>8} "
            f"{s.regime:>8} {s.volume_estimate:>5} {s.priority_score:>9.2f}"
        )
    lines.append("")

    # By-regime summary
    by_regime: dict[str, int] = {"narrow": 0, "medium": 0, "high": 0, "unknown": 0}
    for s in report.sites:
        by_regime[s.regime] = by_regime.get(s.regime, 0) + 1
    lines.append("By regime:")
    for regime, count in by_regime.items():
        if count:
            lines.append(f"  {regime:>8}: {count}")
    lines.append("")

    # Cohort-comparison line. Best-effort: silently suppressed when
    # there isn't enough cohort signal yet (cohort_size < 10) or the
    # median field hasn't been populated server-side. As enrollment
    # grows past launch, this line unfurls naturally.
    cohort_line = _format_cohort_comparison(report)
    if cohort_line:
        lines.append(cohort_line)
        lines.append("")

    lines.append(
        "Next step: `postrule init <file>:<function> --author @you:team` "
        "to wrap the highest-priority site."
    )

    # M3 nudge: invite signup at a moment of revealed value. Suppressed
    # silently when the user is already signed in — they already get the
    # benefit. Failures (auth import unavailable, etc.) absorb silently;
    # an analyzer run never breaks because of a side-channel nudge.
    nudge_line = _format_login_nudge()
    if nudge_line:
        lines.append("")
        lines.append(nudge_line)
    return "\n".join(lines)


def _format_login_nudge() -> str | None:
    """Return a one-line `postrule login` invitation, or ``None`` to suppress.

    Suppresses when the user is already signed in (the cohort + audit-chain
    upside is already theirs). Surfaces when not — the analyzer's report is
    the moment a fresh visitor first sees concrete value in their own code,
    and signup is the lightest action that compounds that value across runs.
    """
    try:
        from postrule.auth import is_logged_in
    except Exception:  # noqa: BLE001 — never fail analyze on a nudge
        return None  # pragma: no cover — defensive against missing auth module
    try:
        if is_logged_in():
            return None
    except Exception:  # noqa: BLE001
        return None
    return (
        "Tip: `postrule login` (free) saves this analysis to postrule.ai, "
        "shares it with teammates, and pulls cohort-tuned defaults. "
        "30 seconds, GitHub OAuth, no card."
    )


def _format_cohort_comparison(report: AnalyzerReport) -> str | None:
    """Render a one-line cohort comparison, or ``None`` to suppress.

    Suppresses when cohort signal is too thin (< 10 deployments) or the
    median field isn't set yet — the latter is the launch state, the
    former covers early-cohort weeks. Both conditions resolve naturally
    as enrollment grows; no code change needed.
    """
    try:
        from postrule.insights import load_cached_or_baked_in

        defaults = load_cached_or_baked_in()
    except Exception:  # noqa: BLE001 — never fail analyze on a cohort fetch
        return None
    if defaults.cohort_size < 10:
        return None
    median = defaults.median_high_priority_density
    if median is None:
        return None
    n = len(report.sites)
    if n == 0:
        return None
    high_priority = sum(1 for s in report.sites if s.priority_score >= 4.0)
    your_density = high_priority / n
    direction = "above" if your_density > median else "at or below"
    return (
        f"Cohort comparison (n={defaults.cohort_size:,} deployments):\n"
        f"  high-priority density: {your_density:.0%} "
        f"(cohort median: {median:.0%}) — {direction} median."
    )


def render_json(
    report: AnalyzerReport,
    *,
    sort_key: str = "priority",
    reverse: bool = False,
) -> str:
    """Machine-readable report for CI diff tracking."""
    return json.dumps(
        {
            "root": report.root,
            "files_scanned": report.files_scanned,
            "total_sites": report.total_sites(),
            "sites": [asdict(s) for s in report.sort_sites(key=sort_key, reverse=reverse)],
            "errors": report.errors,
            "already_dendrified_count": report.already_dendrified_count(),
            "already_dendrified": [
                {"file_path": fp, "function_name": fn, "line_start": line}
                for fp, fn, line in report.already_dendrified
            ],
        },
        indent=2,
    )


# ---------------------------------------------------------------------------
# Savings projection — analyzer × cost model
# ---------------------------------------------------------------------------


_VOLUME_TO_MONTHLY_CALLS = {
    "cold": 300_000,  # script / migration / cli — bursty, low total
    "warm": 1_300_000,  # default mid-market internal classifier
    "hot": 3_000_000,  # web-route or scheduled hot path
}


def _estimate_monthly_classifications(site: ClassificationSite) -> int:
    """Rough projection of per-site monthly traffic from static signals.

    Keyed off ``site.volume_estimate`` (cold / warm / hot) which the
    analyzer derives from AST signals (route decorators, file path).
    The dynamic-mode measurement wrapper (v1.1) replaces this with
    real per-site call counts from the audit chain.
    """
    return _VOLUME_TO_MONTHLY_CALLS.get(site.volume_estimate, 1_300_000)


@dataclass
class SavingsProjection:
    """Per-site projected annual value. All figures are ranges."""

    site: ClassificationSite
    monthly_classifications_est: int
    engineering_savings_low_usd: float
    engineering_savings_high_usd: float
    token_savings_low_usd: float
    token_savings_high_usd: float
    regression_avoidance_low_usd: float
    regression_avoidance_high_usd: float
    total_low_usd: float
    total_high_usd: float


def project_savings(
    report: AnalyzerReport,
    *,
    eng_cost_per_week_usd: float = 4_000.0,
    baseline_weeks: tuple[float, float] = (1.6, 3.5),
    postrule_weeks: float = 0.1,
    tokens_per_call: tuple[int, int] = (80, 5),  # input, output
    llm_price_per_1m_in_usd: tuple[float, float] = (0.15, 3.00),
    llm_price_per_1m_out_usd: tuple[float, float] = (0.60, 15.00),
    pct_traffic_counterfactual_llm: float = 1.0,
    regressions_per_site_per_year: float = 0.25,
    regression_cost_range_usd: tuple[float, float] = (50_000.0, 300_000.0),
) -> list[SavingsProjection]:
    """Project per-site annual savings from static-scan findings.

    Uses the reference cost model from ``postrule.roi`` (same parameters,
    same formula structure). Inputs are exposed as keyword args so
    callers can adjust assumptions without forking this function.
    """
    projections: list[SavingsProjection] = []
    for site in report.by_priority_desc():
        vol = _estimate_monthly_classifications(site)

        eng_low = max(0, (baseline_weeks[0] - postrule_weeks) * eng_cost_per_week_usd)
        eng_high = max(0, (baseline_weeks[1] - postrule_weeks) * eng_cost_per_week_usd)

        calls_per_year = vol * 12
        cf_calls = calls_per_year * pct_traffic_counterfactual_llm
        cost_low = (
            tokens_per_call[0] * llm_price_per_1m_in_usd[0] / 1e6
            + tokens_per_call[1] * llm_price_per_1m_out_usd[0] / 1e6
        )
        cost_high = (
            tokens_per_call[0] * llm_price_per_1m_in_usd[1] / 1e6
            + tokens_per_call[1] * llm_price_per_1m_out_usd[1] / 1e6
        )
        tok_low = cf_calls * cost_low
        tok_high = cf_calls * cost_high

        reg_low = regressions_per_site_per_year * regression_cost_range_usd[0]
        reg_high = regressions_per_site_per_year * regression_cost_range_usd[1]

        projections.append(
            SavingsProjection(
                site=site,
                monthly_classifications_est=vol,
                engineering_savings_low_usd=eng_low,
                engineering_savings_high_usd=eng_high,
                token_savings_low_usd=tok_low,
                token_savings_high_usd=tok_high,
                regression_avoidance_low_usd=reg_low,
                regression_avoidance_high_usd=reg_high,
                total_low_usd=eng_low + tok_low + reg_low,
                total_high_usd=eng_high + tok_high + reg_high,
            )
        )
    return projections


def render_markdown(
    report: AnalyzerReport,
    *,
    projections: list[SavingsProjection] | None = None,
    sort_key: str = "priority",
    reverse: bool = False,
) -> str:
    """Markdown report suitable for CI PR comments and the pricing page.

    Produces a ranked table of classification sites with priority
    scores, volume estimates, and regime labels. When ``projections``
    is supplied, includes per-site annual savings ranges.
    """
    lines: list[str] = []
    lines.append("# Postrule analyzer report")
    lines.append("")
    lines.append(f"- **Root:** `{report.root}`")
    lines.append(f"- **Files scanned:** {report.files_scanned:,}")
    lines.append(f"- **Classification sites found:** {report.total_sites():,}")
    lines.append("")

    if report.errors:
        lines.append(f"## Parse warnings ({len(report.errors)})")
        lines.append("")
        for e in report.errors[:10]:
            lines.append(f"- {e}")
        if len(report.errors) > 10:
            lines.append(f"- ... and {len(report.errors) - 10} more")
        lines.append("")

    if not report.sites:
        lines.append("_No classification sites identified._")
        lines.append("")
        lines.append(
            "> Analyzer v1 ships 6 AST patterns (P1-P6). If you expected "
            "matches in this repo, file an issue with an anonymized snippet "
            "and we'll add the pattern."
        )
        return "\n".join(lines)

    lines.append("## Sites ranked by wrap priority")
    lines.append("")
    lines.append("| File:Line | Function | Pattern | Labels | Regime | Volume | Priority |")
    lines.append("|---|---|---|---:|---|---|---:|")
    for s in report.sort_sites(key=sort_key, reverse=reverse):
        file_label = f"`{s.file_path}:{s.line_start}`"
        lines.append(
            f"| {file_label} | `{s.function_name}` | "
            f"{s.pattern} | {s.label_cardinality} | "
            f"{s.regime} | {s.volume_estimate} | {s.priority_score:.2f} |"
        )
    lines.append("")

    if projections:
        lines.append("## Projected annual value by site")
        lines.append("")
        lines.append(
            "_Ranges use the default cost model from "
            "`postrule.roi.ROIAssumptions`. Pass your own assumptions to "
            "`project_savings()` to recalculate._"
        )
        lines.append("")
        lines.append("| File:Line | Vol/mo | Eng ($) | Token ($) | Regression ($) | Total ($) |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        total_low = total_high = 0.0
        for p in projections:
            s = p.site
            file_label = f"`{s.file_path}:{s.line_start}`"
            vol_str = (
                f"{p.monthly_classifications_est / 1_000_000:.1f}M"
                if p.monthly_classifications_est >= 1_000_000
                else f"{p.monthly_classifications_est:,}"
            )
            lines.append(
                f"| {file_label} | {vol_str} | "
                f"${p.engineering_savings_low_usd:,.0f}"
                f"–${p.engineering_savings_high_usd:,.0f} | "
                f"${p.token_savings_low_usd:,.0f}"
                f"–${p.token_savings_high_usd:,.0f} | "
                f"${p.regression_avoidance_low_usd:,.0f}"
                f"–${p.regression_avoidance_high_usd:,.0f} | "
                f"${p.total_low_usd:,.0f}"
                f"–${p.total_high_usd:,.0f} |"
            )
            total_low += p.total_low_usd
            total_high += p.total_high_usd
        lines.append("")
        lines.append(
            f"**Portfolio projected value: ${total_low:,.0f}–${total_high:,.0f} per year.**"
        )
        lines.append("")

    # Regime summary
    by_regime: dict[str, int] = {}
    for s in report.sites:
        by_regime[s.regime] = by_regime.get(s.regime, 0) + 1
    lines.append("## By regime")
    lines.append("")
    for regime in ("narrow", "medium", "high", "unknown"):
        if by_regime.get(regime, 0):
            lines.append(f"- **{regime}:** {by_regime[regime]} sites")
    lines.append("")

    lines.append("## Next step")
    lines.append("")
    lines.append("```bash\npostrule init <file>:<function> --author @you:team\n```")
    lines.append("")
    lines.append(
        "Wrap the highest-priority site with the `@ml_switch` decorator. "
        "Zero behavior change at Phase 0; outcome log captures every "
        "classification for later graduation."
    )
    lines.append("")

    return "\n".join(lines)


__all__ = [
    "AnalyzerReport",
    "ClassificationSite",
    "Hazard",
    "HazardAnalysis",
    "LiftStatus",
    "SavingsProjection",
    "analyze",
    "analyze_function_source",
    "project_savings",
    "render_json",
    "render_markdown",
    "render_text",
]


# ===========================================================================
# Phase 5: prescriptive hazard detection
#
# The analyzer's per-site priority score answers "is this worth wrapping?".
# Phase 5 answers "what's blocking auto-lift, and what's the minimum diff to
# fix it?". Each detected site can be paired with a HazardAnalysis whose
# `lift_status` and `hazards` drive both the CLI's prescriptive output and
# the lifters' refusal decisions. The analyzer never silently disagrees with
# the lifters: the detection logic here MUST stay in lockstep with
# `postrule.lifters.*` refusal reasons.
# ===========================================================================


class LiftStatus(_enum.Enum):
    """Whether `postrule init --auto-lift` would succeed on this site."""

    AUTO_LIFTABLE = "auto_liftable"  # safe to lift today
    NEEDS_ANNOTATION = "needs_annotation"  # liftable with explicit annotation
    REFUSED = "refused"  # cannot lift; structural issue
    ALREADY_DENDRIFIED = "already_dendrified"  # already wrapped (decorator/Switch subclass)


@dataclass
class Hazard:
    """One reason a site is hard or unsafe to auto-lift.

    Each hazard names a `category`, a 1-based source `line` where the
    issue lives, a `reason` describing what was found, and a
    `suggested_fix` describing the minimum diff that would unblock
    auto-lifting. `severity` is "warn" for needs-annotation cases and
    "error" for refusals.
    """

    category: str
    line: int
    reason: str
    suggested_fix: str
    severity: str = "warn"


@dataclass
class HazardAnalysis:
    """Per-function hazard report. Returned by `analyze_function_source`."""

    function_name: str
    lift_status: LiftStatus
    hazards: list[Hazard] = field(default_factory=list)


# ----- Hazard detectors ------------------------------------------------------


def _detect_zero_arg_no_return(fn: ast.FunctionDef) -> list[Hazard]:
    """Pytest-style: zero args + no `return` = not a classifier."""
    has_args = bool(fn.args.args) or fn.args.vararg or fn.args.kwarg or bool(fn.args.kwonlyargs)
    has_return = any(
        isinstance(node, ast.Return) and node.value is not None for node in ast.walk(fn)
    )
    if not has_args and not has_return:
        return [
            Hazard(
                category="not_a_classifier",
                line=fn.lineno,
                reason=(
                    f"Function {fn.name!r} takes no inputs and produces no "
                    "return value. Looks like a test or fixture, not a "
                    "classifier."
                ),
                suggested_fix=(
                    "Don't wrap this function. If you intend it as a "
                    "classifier, add at least one input parameter and a "
                    "string-returning branch."
                ),
                severity="error",
            )
        ]
    return []


def _detect_eval_exec(fn: ast.FunctionDef) -> list[Hazard]:
    """eval/exec block static evidence detection."""
    out: list[Hazard] = []
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("eval", "exec")
        ):
            out.append(
                Hazard(
                    category="eval_exec",
                    line=node.lineno,
                    reason=(
                        f"Use of {node.func.id}() at line {node.lineno} "
                        "makes the function's evidence non-static. The LLM "
                        "and ML head can't see what was evaluated."
                    ),
                    suggested_fix=(
                        f"Replace {node.func.id}() with an explicit "
                        "expression so the inputs to the decision are "
                        "visible to the lifter."
                    ),
                    severity="error",
                )
            )
    return out


def _detect_dynamic_dispatch(fn: ast.FunctionDef) -> list[Hazard]:
    """getattr-based attribute access blocks the lifter from packing
    evidence statically.
    """
    out: list[Hazard] = []
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
        ):
            out.append(
                Hazard(
                    category="dynamic_dispatch",
                    line=node.lineno,
                    reason=(
                        f"getattr(...) at line {node.lineno} reads an "
                        "attribute by computed name. The lifter can't "
                        "trace what's being read into the evidence schema."
                    ),
                    suggested_fix=(
                        "Replace with explicit attribute access, or "
                        "annotate the evidence input with "
                        "@evidence_inputs(name=lambda ...: ...) so the "
                        "lifter knows what to gather."
                    ),
                    severity="error",
                )
            )
    return out


def _detect_side_effect_evidence(fn: ast.FunctionDef) -> list[Hazard]:
    """Detect `x = some_call(...); if x.attr: ...` where the bound call
    has likely side effects (looks like an API/IO call).

    Heuristic: an Assign whose value is a Call to an attribute on a
    name (`module.method(...)` or `obj.method(...)`), AND the bound
    name is later read in an If test. This is the common
    `response = api.charge(...)` pattern.
    """
    # Indexed by bound-name -> (line, source-call-text) for matched binds.
    binds: dict[str, tuple[int, str]] = {}
    for stmt in fn.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target = stmt.targets[0]
            value = stmt.value
            if (
                isinstance(target, ast.Name)
                and isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
            ):
                # Render call as `<name>.<method>` for the diagnostic.
                func_repr = ast.unparse(value.func)
                binds[target.id] = (stmt.lineno, func_repr)

    if not binds:
        return []

    # Find any If anywhere in the body that reads a bound name.
    out: list[Hazard] = []
    flagged: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        for sub in ast.walk(node.test):
            if isinstance(sub, ast.Name) and sub.id in binds and sub.id not in flagged:
                line, func_repr = binds[sub.id]
                flagged.add(sub.id)
                out.append(
                    Hazard(
                        category="side_effect_evidence",
                        line=line,
                        reason=(
                            f"Line {line} binds {sub.id!r} from "
                            f"{func_repr}(...) and a later branch reads it. "
                            "If the call has side effects, lifting it into "
                            "evidence-gathering would re-fire those side "
                            "effects on every dispatch."
                        ),
                        suggested_fix=(
                            f"If {func_repr} supports a dry-run mode, use "
                            "it for the probe and move the real call into "
                            "the chosen label's _on_ handler. If not, add "
                            "@evidence_via_probe(field='...') to declare "
                            "this as safe."
                        ),
                        severity="error",
                    )
                )
    return out


def _detect_multi_arg_no_annotation(fn: ast.FunctionDef) -> list[Hazard]:
    """Multi-arg functions without type hints can't generate a typed
    evidence dataclass. Liftable IF the user adds annotations.
    """
    args = list(fn.args.args)
    # Strip self/cls so methods aren't penalized.
    if args and args[0].arg in ("self", "cls"):
        args = args[1:]
    if len(args) < 2:
        return []
    missing = [a.arg for a in args if a.annotation is None]
    if not missing:
        return []
    return [
        Hazard(
            category="multi_arg_no_annotation",
            line=fn.lineno,
            reason=(
                f"Function {fn.name!r} takes {len(args)} args but "
                f"{len(missing)} have no type annotation: "
                f"{', '.join(missing)}. Multi-arg auto-packing needs "
                "annotations to build the evidence dataclass schema."
            ),
            suggested_fix=(
                "Add type hints to each parameter, e.g. "
                f"`def {fn.name}({', '.join(a.arg + ': str' for a in args)}):`. "
                "Single-arg functions are exempt for back-compat."
            ),
            severity="warn",
        )
    ]


# ----- Public Phase 5 entrypoint --------------------------------------------


_HAZARD_DETECTORS: list[Callable[[ast.FunctionDef], list[Hazard]]] = [
    _detect_zero_arg_no_return,
    _detect_eval_exec,
    _detect_dynamic_dispatch,
    _detect_side_effect_evidence,
    _detect_multi_arg_no_annotation,
]


def analyze_function_source(source: str, function_name: str) -> HazardAnalysis:
    """Run hazard detection on a single function defined in ``source``.

    Returns a :class:`HazardAnalysis` whose ``lift_status`` summarizes the
    most-restrictive verdict across all detected hazards (REFUSED beats
    NEEDS_ANNOTATION beats AUTO_LIFTABLE) and whose ``hazards`` is the
    full list of findings.

    Raises ``ValueError`` if the function name is not found at the top
    level of ``source``.
    """
    tree = ast.parse(source)
    target: ast.FunctionDef | None = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            target = node
            break
    if target is None:
        raise ValueError(
            f"Function {function_name!r} not found at the top level of the supplied source."
        )

    if _has_ml_switch_decorator(target):
        return HazardAnalysis(
            function_name=function_name,
            lift_status=LiftStatus.ALREADY_DENDRIFIED,
            hazards=[],
        )

    hazards: list[Hazard] = []
    for detector in _HAZARD_DETECTORS:
        hazards.extend(detector(target))

    if any(h.severity == "error" for h in hazards):
        status = LiftStatus.REFUSED
    elif hazards:
        status = LiftStatus.NEEDS_ANNOTATION
    else:
        status = LiftStatus.AUTO_LIFTABLE

    return HazardAnalysis(
        function_name=function_name,
        lift_status=status,
        hazards=hazards,
    )
