# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for dendra.insights.fingerprint."""

from __future__ import annotations

import pytest

from dendra.insights.fingerprint import (
    fingerprint_function,
    fingerprint_repo_files,
)


class TestFingerprintFunction:
    def test_same_function_produces_same_fingerprint(self):
        src = (
            "def classify(text):\n"
            "    if 'a' in text: return 'alpha'\n"
            "    if 'b' in text: return 'beta'\n"
            "    return 'other'\n"
        )
        fp_a = fingerprint_function(src)
        fp_b = fingerprint_function(src)
        assert fp_a == fp_b
        assert len(fp_a) == 32  # 16 bytes * 2 hex chars

    def test_renamed_variables_produce_same_fingerprint(self):
        # The fingerprint must NOT depend on identifier names.
        src_a = "def classify(text):\n    if 'a' in text: return 'alpha'\n    return 'other'\n"
        src_b = (
            "def my_function(input_str):\n"
            "    if 'a' in input_str: return 'alpha'\n"
            "    return 'other'\n"
        )
        # NOTE: function names AND argument names differ, but the
        # AST shape (FunctionDef → If → Compare → Return-string-literal)
        # is identical. After identifier stripping, fingerprints match.
        assert fingerprint_function(src_a) == fingerprint_function(src_b)

    def test_changed_label_values_produce_same_fingerprint(self):
        # Label values are content. Two functions returning different
        # labels but with the same control-flow shape must fingerprint
        # the same (content-leak protection).
        src_a = "def f(t):\n    if 'a' in t: return 'alpha'\n    return 'beta'\n"
        src_b = (
            "def f(t):\n    if 'urgent' in t: return 'priority_high'\n    return 'priority_low'\n"
        )
        assert fingerprint_function(src_a) == fingerprint_function(src_b)

    def test_different_control_flow_produces_different_fingerprint(self):
        src_a = "def f(t):\n    if 'a' in t: return 'x'\n    return 'y'\n"
        # Different shape: two ifs instead of one + fallthrough.
        src_b = (
            "def f(t):\n    if 'a' in t: return 'x'\n    if 'b' in t: return 'y'\n    return 'z'\n"
        )
        assert fingerprint_function(src_a) != fingerprint_function(src_b)

    def test_async_function_is_supported(self):
        src = "async def aclassify(text):\n    if 'a' in text: return 'alpha'\n    return 'other'\n"
        fp = fingerprint_function(src)
        assert len(fp) == 32

    def test_no_function_in_source_raises(self):
        with pytest.raises(ValueError, match="no top-level function"):
            fingerprint_function("x = 1\nprint(x)\n")

    def test_syntax_error_raises(self):
        with pytest.raises(ValueError, match="did not parse"):
            fingerprint_function("def broken(:\n    return 1\n")

    def test_numeric_literals_dont_leak(self):
        # Two functions identical except for numeric thresholds —
        # must fingerprint the same since numbers are content.
        src_a = "def f(x):\n    if x > 10: return 'big'\n    return 'small'\n"
        src_b = "def f(x):\n    if x > 99999: return 'big'\n    return 'small'\n"
        assert fingerprint_function(src_a) == fingerprint_function(src_b)


class TestFingerprintRepoFiles:
    def test_same_file_set_produces_same_fingerprint(self):
        files = ["src/a.py", "src/b.py", "tests/test_a.py"]
        fp_a = fingerprint_repo_files(files)
        fp_b = fingerprint_repo_files(files)
        assert fp_a == fp_b
        assert len(fp_a) == 16  # 8-byte digest

    def test_order_independent(self):
        files_a = ["src/a.py", "src/b.py", "src/c.py"]
        files_b = ["src/c.py", "src/a.py", "src/b.py"]
        assert fingerprint_repo_files(files_a) == fingerprint_repo_files(files_b)

    def test_dedupes_repeated_paths(self):
        files_a = ["src/a.py", "src/a.py", "src/b.py"]
        files_b = ["src/a.py", "src/b.py"]
        assert fingerprint_repo_files(files_a) == fingerprint_repo_files(files_b)

    def test_different_file_sets_differ(self):
        files_a = ["src/a.py", "src/b.py"]
        files_b = ["src/a.py", "src/c.py"]
        assert fingerprint_repo_files(files_a) != fingerprint_repo_files(files_b)
