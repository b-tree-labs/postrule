# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Code-language detection benchmark.

Vendored under ``data/codelangs/<lang>/*.txt``. Sources documented
in ``data/codelangs/SOURCES.md``. Adds a benchmark that stresses
the system on heavily structured non-English text — including
legacy languages (FORTRAN) most ML pipelines ignore.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data" / "codelangs"

EXPECTED_LANGS = {
    "fortran",
    "python",
    "javascript",
    "java",
    "c",
    "cpp",
    "go",
    "rust",
    "ruby",
    "typescript",
    "kotlin",
    "mojo",
}


@pytest.mark.benchmark
class TestCodelangsLoader:
    def test_loader_is_importable(self):
        try:
            from dendra.benchmarks.loaders import load_codelangs
        except ImportError:
            pytest.fail("dendra.benchmarks.loaders.load_codelangs not implemented yet")
        assert callable(load_codelangs)

    def test_data_directory_present(self):
        assert DATA_DIR.exists(), (
            "data/codelangs/ does not exist; run scripts/fetch_codelangs.py "
            "to populate it before running the benchmark"
        )
        sources_doc = DATA_DIR / "SOURCES.md"
        assert sources_doc.exists(), (
            "data/codelangs/SOURCES.md must document upstream sources + licenses"
        )

    def test_returns_ten_canonical_languages(self):
        from dendra.benchmarks.loaders import load_codelangs

        ds = load_codelangs()
        assert ds.name == "codelangs"
        labels = set(ds.labels)
        assert labels == EXPECTED_LANGS, (
            f"unexpected codelangs labels — got {labels}, expected {EXPECTED_LANGS}"
        )

    def test_pairs_have_string_text_and_known_labels(self):
        from dendra.benchmarks.loaders import load_codelangs

        ds = load_codelangs()
        for text, label in (ds.train + ds.test)[:50]:
            assert isinstance(text, str) and text
            assert label in EXPECTED_LANGS, label

    def test_fortran_inputs_look_like_fortran(self):
        """Cheap sanity check: at least one FORTRAN sample should contain
        a Fortran-distinctive token (PROGRAM, SUBROUTINE, MODULE, IMPLICIT)
        case-insensitive."""
        from dendra.benchmarks.loaders import load_codelangs

        ds = load_codelangs()
        fortran_samples = [t for t, lbl in (ds.train + ds.test) if lbl == "fortran"]
        assert fortran_samples, "no FORTRAN samples found"
        joined = "\n".join(fortran_samples).upper()
        fortran_tokens = ["PROGRAM", "SUBROUTINE", "MODULE", "IMPLICIT", "FUNCTION"]
        assert any(tok in joined for tok in fortran_tokens), (
            f"FORTRAN samples don't contain any of {fortran_tokens}; "
            f"either the loader picked up the wrong files or the fetch "
            f"script vendored non-FORTRAN content"
        )


@pytest.mark.benchmark
class TestCodelangsRegistration:
    def test_in_public_init(self):
        import dendra.benchmarks as benchmarks_mod

        assert "load_codelangs" in getattr(benchmarks_mod, "__all__", [])

    def test_in_cli_registry(self):
        from dendra.cli import _BENCHMARKS

        assert "codelangs" in _BENCHMARKS
