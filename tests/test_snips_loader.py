# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Snips benchmark loader: 7 voice-assistant intents.

Snips is the canonical second narrow-domain benchmark in the
intent-classification literature (Coucke et al. 2018), paired with
ATIS in nearly every published comparison. Adding it gives the paper
a second Regime A data point so the "narrow-domain rule baseline"
claim doesn't rest on n=1.
"""

from __future__ import annotations

import pytest

SNIPS_INTENTS = {
    "AddToPlaylist",
    "BookRestaurant",
    "GetWeather",
    "PlayMusic",
    "RateBook",
    "SearchCreativeWork",
    "SearchScreeningEvent",
}


@pytest.mark.benchmark
class TestSnipsLoader:
    """Contract: ``load_snips()`` returns the standard 7-intent Snips
    dataset shaped like every other ``BenchmarkDataset``."""

    def test_loader_is_importable(self):
        try:
            from dendra.benchmarks.loaders import load_snips
        except ImportError:
            pytest.fail("dendra.benchmarks.loaders.load_snips is not implemented yet")
        assert callable(load_snips)

    def test_loader_returns_benchmark_dataset(self):
        from dendra.benchmarks import BenchmarkDataset
        from dendra.benchmarks.loaders import load_snips

        ds = load_snips()
        assert isinstance(ds, BenchmarkDataset)
        assert ds.name == "snips"
        assert ds.train and ds.test, "splits must be non-empty"
        assert ds.citation, "citation must reference Coucke et al. 2018"

    def test_loader_returns_seven_canonical_intents(self):
        from dendra.benchmarks.loaders import load_snips

        ds = load_snips()
        labels = set(ds.labels)
        assert labels == SNIPS_INTENTS, (
            f"unexpected Snips labels — got {labels}, expected {SNIPS_INTENTS}"
        )

    def test_train_and_test_pairs_have_string_text_and_known_labels(self):
        from dendra.benchmarks.loaders import load_snips

        ds = load_snips()
        for text, label in (ds.train + ds.test)[:50]:
            assert isinstance(text, str) and text, "text must be a non-empty string"
            assert label in SNIPS_INTENTS, f"label {label!r} not in canonical Snips intent set"


@pytest.mark.benchmark
class TestSnipsRegistration:
    """The CLI bench registry and the public ``__init__`` re-export
    ``load_snips`` so it's reachable via ``dendra bench snips``."""

    def test_snips_in_public_init_exports(self):
        import dendra.benchmarks as benchmarks_mod

        assert "load_snips" in getattr(benchmarks_mod, "__all__", []), (
            "load_snips must be in dendra.benchmarks.__all__"
        )
        assert hasattr(benchmarks_mod, "load_snips")

    def test_snips_in_cli_registry(self):
        import dendra.cli as cli_mod

        # The bench command resolves a slug → loader name. Snips must
        # be registered so ``dendra bench snips`` works.
        registry = getattr(cli_mod, "_BENCH_LOADERS", None)
        if registry is None:
            registry = getattr(cli_mod, "BENCHMARKS", None)
        if registry is None:
            for name in dir(cli_mod):
                obj = getattr(cli_mod, name, None)
                if isinstance(obj, dict) and "atis" in obj:
                    registry = obj
                    break
        assert registry is not None, "could not locate the CLI bench registry"
        assert "snips" in registry, (
            f"'snips' must be in the CLI bench registry; got keys {list(registry.keys())}"
        )
