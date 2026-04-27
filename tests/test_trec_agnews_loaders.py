# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""TREC-6 and AG News loaders.

Two new datasets covering stress axes the existing 5 benchmarks
don't:

- **TREC-6** (Li & Roth 2002): 6 question categories with strong
  question-word keyword affinity. Counterpoint to Snips (also low
  cardinality, but strong rule-keyword signal).
- **AG News** (Zhang et al. 2015): 4 news categories with
  multi-sentence article inputs. Counterpoint to ATIS (utterance-
  shaped text); tests whether the transition curve generalizes to
  longer, paragraph-scale text.
"""

from __future__ import annotations

import pytest

TREC6_LABELS = {"DESC", "ENTY", "ABBR", "HUM", "NUM", "LOC"}
AG_NEWS_LABELS = {"World", "Sports", "Business", "Sci/Tech"}


@pytest.mark.benchmark
class TestTrec6Loader:
    def test_loader_is_importable(self):
        try:
            from dendra.benchmarks.loaders import load_trec6
        except ImportError:
            pytest.fail("dendra.benchmarks.loaders.load_trec6 not implemented yet")
        assert callable(load_trec6)

    def test_returns_six_canonical_categories(self):
        from dendra.benchmarks.loaders import load_trec6

        ds = load_trec6()
        assert ds.name == "trec6"
        assert ds.train and ds.test
        labels = set(ds.labels)
        assert labels == TREC6_LABELS, (
            f"unexpected TREC-6 labels — got {labels}, expected {TREC6_LABELS}"
        )

    def test_pairs_have_string_text_and_known_labels(self):
        from dendra.benchmarks.loaders import load_trec6

        ds = load_trec6()
        for text, label in (ds.train + ds.test)[:50]:
            assert isinstance(text, str) and text
            assert label in TREC6_LABELS, label


@pytest.mark.benchmark
class TestAgNewsLoader:
    def test_loader_is_importable(self):
        try:
            from dendra.benchmarks.loaders import load_ag_news
        except ImportError:
            pytest.fail("dendra.benchmarks.loaders.load_ag_news not implemented yet")
        assert callable(load_ag_news)

    def test_returns_four_canonical_categories(self):
        from dendra.benchmarks.loaders import load_ag_news

        ds = load_ag_news()
        assert ds.name == "ag_news"
        assert ds.train and ds.test
        labels = set(ds.labels)
        assert labels == AG_NEWS_LABELS, (
            f"unexpected AG News labels — got {labels}, expected {AG_NEWS_LABELS}"
        )

    def test_inputs_are_longer_than_utterance_length(self):
        """AG News articles should be substantially longer than ATIS-style
        utterances. This is the stress axis that motivates including AG News."""
        from dendra.benchmarks.loaders import load_ag_news

        ds = load_ag_news()
        sample = ds.train[:200]
        avg_len = sum(len(text.split()) for text, _ in sample) / len(sample)
        assert avg_len > 20, (
            f"AG News inputs are unexpectedly short (avg {avg_len:.1f} words); "
            f"loader may have grabbed only headlines"
        )


@pytest.mark.benchmark
class TestRegistration:
    def test_both_in_public_init(self):
        import dendra.benchmarks as benchmarks_mod

        exported = getattr(benchmarks_mod, "__all__", [])
        assert "load_trec6" in exported
        assert "load_ag_news" in exported

    def test_both_in_cli_registry(self):
        from dendra.cli import _BENCHMARKS

        assert "trec6" in _BENCHMARKS
        assert "ag_news" in _BENCHMARKS
