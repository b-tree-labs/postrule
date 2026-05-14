# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Tests for benchmark dataset loaders.

Shape tests (dataclass contract) run unconditionally. Real-load tests are
skipped when the optional ``datasets`` library is not installed, or when
network is unavailable.
"""

from __future__ import annotations

import sys

import pytest

from postrule.benchmarks import (
    BenchmarkDataset,
    load_atis,
    load_banking77,
    load_clinc150,
    load_hwu64,
)
from postrule.benchmarks import loaders as _loaders

# ---------------------------------------------------------------------------
# Unconditional shape tests
# ---------------------------------------------------------------------------


def test_benchmark_dataset_shape():
    ds = BenchmarkDataset(
        name="x",
        train=[("hello", "greet")],
        test=[("bye", "farewell")],
        labels=["greet", "farewell"],
        citation="stub",
    )
    assert ds.name == "x"
    assert ds.train == [("hello", "greet")]
    assert ds.test == [("bye", "farewell")]
    assert ds.labels == ["greet", "farewell"]
    assert ds.citation == "stub"


def test_benchmark_dataset_defaults():
    ds = BenchmarkDataset(name="y", train=[], test=[])
    assert ds.labels == []
    assert ds.citation == ""


def test_loaders_are_exported():
    for fn in (load_atis, load_banking77, load_clinc150, load_hwu64):
        assert callable(fn)


# ---------------------------------------------------------------------------
# Missing-dependency behavior — synthetically hide `datasets`
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "loader",
    [load_banking77, load_clinc150, load_hwu64, load_atis],
)
def test_loader_raises_clear_import_error_when_datasets_missing(loader, monkeypatch):
    """If the `datasets` library isn't importable, loaders raise a helpful ImportError."""

    real_import = __import__

    def fake_import(name, *a, **kw):
        if name == "datasets" or name.startswith("datasets."):
            raise ImportError("No module named 'datasets'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr("builtins.__import__", fake_import)
    monkeypatch.delitem(sys.modules, "datasets", raising=False)

    with pytest.raises(ImportError) as exc:
        loader()
    assert "pip install postrule[bench]" in str(exc.value)


# ---------------------------------------------------------------------------
# Real-load smoke tests — skipped without `datasets` installed
# ---------------------------------------------------------------------------


def _mock_hf_split(rows, label_names, label_key="label"):
    """Build a minimal duck-typed stand-in for a HuggingFace Dataset split."""

    class _Feat:
        names = label_names

    class _Split:
        features = {label_key: _Feat(), "text": object(), "intent": _Feat()}

        def __iter__(self):
            return iter(rows)

    return _Split()


def test_load_banking77_shape_with_mocked_datasets(monkeypatch):
    pytest.importorskip("datasets")
    labels = ["card_arrival", "pin_blocked"]
    rows = [{"text": "where is my card", "label": 0}, {"text": "pin locked", "label": 1}]
    split = _mock_hf_split(rows, labels, "label")
    monkeypatch.setattr(_loaders, "_load", lambda *a, **kw: {"train": split, "test": split})

    ds = load_banking77()
    assert isinstance(ds, BenchmarkDataset)
    assert ds.name == "banking77"
    assert ds.labels == labels
    assert ds.train[0] == ("where is my card", "card_arrival")
    assert "Casanueva" in ds.citation


def test_load_clinc150_shape_with_mocked_datasets(monkeypatch):
    pytest.importorskip("datasets")
    labels = ["translate", "oos"]
    rows = [{"text": "translate hello", "intent": 0}, {"text": "???", "intent": 1}]
    split = _mock_hf_split(rows, labels, "intent")
    monkeypatch.setattr(_loaders, "_load", lambda *a, **kw: {"train": split, "test": split})

    ds = load_clinc150()
    assert ds.name == "clinc150"
    assert ds.labels == labels
    assert ds.train[0][1] == "translate"


def test_load_hwu64_shape_with_mocked_datasets(monkeypatch):
    pytest.importorskip("datasets")
    labels = ["alarm_set", "calendar_query"]
    rows = [{"text": "set alarm", "label": 0}]
    split = _mock_hf_split(rows, labels, "label")
    monkeypatch.setattr(_loaders, "_load", lambda *a, **kw: {"train": split, "test": split})

    ds = load_hwu64()
    assert ds.name == "hwu64"
    assert ds.train[0] == ("set alarm", "alarm_set")


def test_load_atis_shape_with_mocked_datasets(monkeypatch):
    pytest.importorskip("datasets")
    labels = ["flight", "airfare"]
    rows = [{"text": "show me flights", "intent": 0}]
    split = _mock_hf_split(rows, labels, "intent")
    monkeypatch.setattr(_loaders, "_load", lambda *a, **kw: {"train": split, "test": split})

    ds = load_atis()
    assert ds.name == "atis"
    assert ds.train[0] == ("show me flights", "flight")
    assert "ATIS" in ds.citation
