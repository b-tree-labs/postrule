# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Loaders for public intent-classification benchmarks.

Each loader returns a :class:`BenchmarkDataset` with a uniform shape so
downstream Postrule evaluation code doesn't care which corpus it's using.

The optional ``datasets`` library (HuggingFace) is imported lazily per-call;
if it isn't installed a clear ImportError is raised pointing the user at the
``bench`` extra.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CACHE_DIR = Path.home() / ".cache" / "postrule" / "datasets"

_INSTALL_HINT = (
    "The 'datasets' library is required for Postrule benchmark loaders. "
    "Install it with:  pip install postrule[bench]"
)


@dataclass
class BenchmarkDataset:
    """Uniform shape for all four intent-classification benchmarks."""

    name: str
    train: list[tuple[str, str]]
    test: list[tuple[str, str]]
    labels: list[str] = field(default_factory=list)
    citation: str = ""


def _require_datasets() -> Any:
    try:
        from datasets import load_dataset  # type: ignore[import-not-found]
    except ImportError as e:  # pragma: no cover
        raise ImportError(_INSTALL_HINT) from e
    return load_dataset


def _load(hf_id: str, config: str | None = None) -> Any:
    load_dataset = _require_datasets()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if config:
        return load_dataset(hf_id, config, cache_dir=str(CACHE_DIR))
    return load_dataset(hf_id, cache_dir=str(CACHE_DIR))


def _rows_to_pairs(
    split: Any, text_key: str, label_key: str, label_names: list[str]
) -> list[tuple[str, str]]:
    """Normalize a split to (text, label_string) pairs.

    Handles both ``ClassLabel`` (int + names) and plain string labels.
    """
    out: list[tuple[str, str]] = []
    for row in split:
        raw = row[label_key]
        label = label_names[raw] if isinstance(raw, int) and label_names else str(raw)
        out.append((row[text_key], label))
    return out


def _collect_string_labels(pairs: list[tuple[str, str]]) -> list[str]:
    seen: list[str] = []
    s = set()
    for _, lbl in pairs:
        if lbl not in s:
            s.add(lbl)
            seen.append(lbl)
    return sorted(seen)


def load_banking77() -> BenchmarkDataset:
    """Banking77 (Casanueva et al. 2020) — 77 fine-grained banking intents."""
    ds = _load("banking77")
    label_feat = ds["train"].features["label"]
    names = list(getattr(label_feat, "names", []))
    train = _rows_to_pairs(ds["train"], "text", "label", names)
    test = _rows_to_pairs(ds["test"], "text", "label", names)
    labels = names or _collect_string_labels(train + test)
    return BenchmarkDataset(
        name="banking77",
        train=train,
        test=test,
        labels=labels,
        citation="Casanueva et al. 2020, 'Efficient Intent Detection with Dual Sentence Encoders'",
    )


def load_clinc150() -> BenchmarkDataset:
    """CLINC150 (Larson et al. 2019) — 150 intents + out-of-scope = 151 labels."""
    ds = _load("clinc_oos", "plus")
    label_feat = ds["train"].features["intent"]
    names = list(getattr(label_feat, "names", []))
    train = _rows_to_pairs(ds["train"], "text", "intent", names)
    test = _rows_to_pairs(ds["test"], "text", "intent", names)
    labels = names or _collect_string_labels(train + test)
    return BenchmarkDataset(
        name="clinc150",
        train=train,
        test=test,
        labels=labels,
        citation=(
            "Larson et al. 2019, 'An Evaluation Dataset for Intent "
            "Classification and Out-of-Scope Prediction'"
        ),
    )


def _maybe_names(feat: Any) -> list[str]:
    return list(getattr(feat, "names", []) or [])


def load_hwu64() -> BenchmarkDataset:
    """HWU64 (Liu et al. 2019) — 64 intents across 21 scenarios."""
    ds = _load("FastFit/hwu_64")
    names = _maybe_names(ds["train"].features["label"])
    train = _rows_to_pairs(ds["train"], "text", "label", names)
    test_split = ds["test"] if "test" in ds else ds["validation"]
    test = _rows_to_pairs(test_split, "text", "label", names)
    labels = names or _collect_string_labels(train + test)
    return BenchmarkDataset(
        name="hwu64",
        train=train,
        test=test,
        labels=labels,
        citation=(
            "Liu et al. 2019, 'Benchmarking Natural Language Understanding "
            "Services for Building Conversational Agents'"
        ),
    )


def load_atis() -> BenchmarkDataset:
    """ATIS — Air Travel Information System; ~17–26 flight-domain intents."""
    ds = _load("tuetschek/atis")
    names = _maybe_names(ds["train"].features["intent"])
    train = _rows_to_pairs(ds["train"], "text", "intent", names)
    test_split = ds["test"] if "test" in ds else ds["validation"]
    test = _rows_to_pairs(test_split, "text", "intent", names)
    labels = names or _collect_string_labels(train + test)
    return BenchmarkDataset(
        name="atis",
        train=train,
        test=test,
        labels=labels,
        citation="Hemphill et al. 1990, 'The ATIS Spoken Language Systems Pilot Corpus'",
    )


def load_trec6() -> BenchmarkDataset:
    """TREC-6 (Li & Roth 2002) — 6 coarse question categories.

    Strong question-word keyword affinity (e.g. "what" → DESC,
    "where" → LOC), making it the natural counterpoint to Snips: also
    low-cardinality but with the rule baseline that Snips lacks.

    Sourced from ``SetFit/TREC-QC`` which exposes the canonical coarse
    label strings ("DESC", "ENTY", "ABBR", "HUM", "NUM", "LOC") via
    ``label_coarse_original`` and bypasses the legacy script-based
    ``trec`` HF dataset that newer ``datasets`` versions refuse.
    """
    ds = _load("SetFit/TREC-QC")

    def _to_pairs(split):
        return [(row["text"], row["label_coarse_original"]) for row in split]

    train = _to_pairs(ds["train"])
    test_split = ds["test"] if "test" in ds else ds["validation"]
    test = _to_pairs(test_split)
    labels = _collect_string_labels(train + test)
    return BenchmarkDataset(
        name="trec6",
        train=train,
        test=test,
        labels=labels,
        citation="Li & Roth 2002, 'Learning Question Classifiers'",
    )


def load_ag_news() -> BenchmarkDataset:
    """AG News (Zhang et al. 2015) — 4 broad news categories.

    Multi-sentence article-shaped inputs (50–200 words) vs the
    utterance-shaped text in ATIS/Snips. Tests whether the
    transition-curve story generalizes to longer text.
    """
    ds = _load("ag_news")
    label_feat = ds["train"].features["label"]
    raw_names = list(getattr(label_feat, "names", []))
    # AG News canonical labels are "World", "Sports", "Business", "Sci/Tech";
    # the HF dataset stores them as e.g. "World" and "Sci/Tech" already, but
    # some mirrors lowercase or rename. Normalize.
    canonical = {
        "world": "World",
        "sports": "Sports",
        "business": "Business",
        "sci/tech": "Sci/Tech",
        "science/technology": "Sci/Tech",
        "tech": "Sci/Tech",
    }
    names = [canonical.get(n.lower(), n) for n in raw_names]
    train = _rows_to_pairs(ds["train"], "text", "label", names)
    test = _rows_to_pairs(ds["test"], "text", "label", names)
    labels = names or _collect_string_labels(train + test)
    return BenchmarkDataset(
        name="ag_news",
        train=train,
        test=test,
        labels=labels,
        citation=(
            "Zhang, Zhao & LeCun 2015, 'Character-level Convolutional "
            "Networks for Text Classification'"
        ),
    )


def load_cifar10(*, train_n: int = 1000, test_n: int = 200) -> BenchmarkDataset:
    """CIFAR-10 (Krizhevsky 2009) — 10-class image classification.

    Stresses the lifecycle on a non-text modality. Each pair is a
    ``(numpy.ndarray of shape (32, 32, 3) uint8, label_string)``.
    The labels are the canonical CIFAR-10 set (airplane, automobile,
    bird, cat, deer, dog, frog, horse, ship, truck).

    Defaults are small (1000 train / 200 test) to keep the bench
    fast. The full corpus is 50k/10k.
    """
    import numpy as np

    ds = _load("cifar10")
    label_feat = ds["train"].features["label"]
    names = list(getattr(label_feat, "names", []))
    if not names:
        raise RuntimeError("CIFAR-10 loader: missing class-name metadata")

    def _split_to_pairs(split, n):
        pairs: list[tuple[Any, str]] = []
        for i, row in enumerate(split):
            if i >= n:
                break
            img = np.asarray(row["img"])
            if img.ndim == 2:  # grayscale → broadcast to RGB
                img = np.stack([img] * 3, axis=-1)
            elif img.shape[-1] == 4:  # RGBA → drop alpha
                img = img[..., :3]
            if img.dtype != np.uint8:
                img = img.astype(np.uint8)
            pairs.append((img, names[row["label"]]))
        return pairs

    train = _split_to_pairs(ds["train"], train_n)
    test = _split_to_pairs(ds["test"], test_n)
    return BenchmarkDataset(
        name="cifar10",
        train=train,
        test=test,
        labels=sorted(names),
        citation=("Krizhevsky 2009, 'Learning Multiple Layers of Features from Tiny Images'"),
    )


def load_codelangs() -> BenchmarkDataset:
    """Codelangs (Postrule-curated, 2026-04) — 10 programming languages.

    Vendored under ``data/codelangs/<lang>/*.txt`` from license-vetted
    open-source upstreams. Documented in ``data/codelangs/SOURCES.md``.

    Stresses the system on heavily-structured non-English text:
    distinctive token distributions per language (camelCase vs
    snake_case vs all-caps FORTRAN keywords, indentation-significant
    Python, brace-and-semicolon C-family, etc.). FORTRAN is included
    deliberately as a legacy-language stress test that most ML
    pipelines underrepresent; sourced from NJOY2016 (LANL nuclear
    data processing code) and LAPACK.
    """
    from pathlib import Path
    from random import Random

    repo_root = Path(__file__).resolve().parents[3]
    data_dir = repo_root / "data" / "codelangs"
    if not data_dir.exists():
        raise FileNotFoundError(
            "data/codelangs/ not found. Run scripts/fetch_codelangs.py to populate it."
        )
    pairs: list[tuple[str, str]] = []
    for lang_dir in sorted(p for p in data_dir.iterdir() if p.is_dir()):
        for f in sorted(lang_dir.glob("*.txt")):
            try:
                text = f.read_text(errors="replace").strip()
            except OSError:
                continue
            if text:
                pairs.append((text, lang_dir.name))
    if not pairs:
        raise RuntimeError(
            "data/codelangs/ exists but contains no samples; re-run scripts/fetch_codelangs.py"
        )
    # Deterministic shuffle + 80/20 split per language to keep test
    # set roughly balanced across languages.
    rng = Random(20260427)
    rng.shuffle(pairs)
    by_lang: dict[str, list[tuple[str, str]]] = {}
    for t, lbl in pairs:
        by_lang.setdefault(lbl, []).append((t, lbl))
    train: list[tuple[str, str]] = []
    test: list[tuple[str, str]] = []
    for _lang, items in by_lang.items():
        split = max(1, int(0.8 * len(items)))
        train.extend(items[:split])
        test.extend(items[split:])
    rng.shuffle(train)
    rng.shuffle(test)
    labels = sorted(by_lang.keys())
    return BenchmarkDataset(
        name="codelangs",
        train=train,
        test=test,
        labels=labels,
        citation=(
            "Postrule-curated (2026), vendored from NJOY2016 (LANL, BSD-3), "
            "LAPACK (modified BSD), CPython (PSF), Node.js (MIT), Apache "
            "Commons (Apache-2.0), musl (MIT), Boost (BSL-1.0), Go (BSD-3), "
            "serde (MIT/Apache-2.0), Ruby (BSD-2), TypeScript (Apache-2.0). "
            "See data/codelangs/SOURCES.md."
        ),
    )


def load_snips() -> BenchmarkDataset:
    """Snips (Coucke et al. 2018) — 7 voice-assistant intents.

    The canonical second narrow-domain benchmark paired with ATIS in
    intent-classification literature. Sourced from the
    ``benayas/snips`` Hugging Face mirror of the standard 7-intent
    NLU split (AddToPlaylist, BookRestaurant, GetWeather, PlayMusic,
    RateBook, SearchCreativeWork, SearchScreeningEvent).
    """
    ds = _load("benayas/snips")
    train_features = ds["train"].features
    text_key = next(
        (k for k in ("text", "sentence", "utterance") if k in train_features),
        None,
    )
    label_key = next(
        (k for k in ("label", "intent", "category") if k in train_features),
        None,
    )
    if text_key is None or label_key is None:
        raise RuntimeError(
            f"Snips loader could not find expected text/label fields; "
            f"got features {list(train_features)}"
        )
    names = _maybe_names(train_features[label_key])
    train = _rows_to_pairs(ds["train"], text_key, label_key, names)
    test_split = ds["test"] if "test" in ds else ds["validation"]
    test = _rows_to_pairs(test_split, text_key, label_key, names)
    labels = names or _collect_string_labels(train + test)
    return BenchmarkDataset(
        name="snips",
        train=train,
        test=test,
        labels=labels,
        citation=(
            "Coucke et al. 2018, 'Snips Voice Platform: an embedded "
            "Spoken Language Understanding system'"
        ),
    )
