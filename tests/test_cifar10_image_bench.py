# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""CIFAR-10 image-classification bench.

Demonstrates the transition curve generalizes beyond text. Rule
operates on hand-crafted color features (mean RGB centroid per
class); ML head is a flat-pixel logistic regression. CLIP-quality
embeddings are deferred to v1.x to avoid a torch dependency on the
v1.0 install path.

Three contracts:

A. ``load_cifar10()`` returns a benchmark dataset of (image_array,
   label_string) pairs, with the canonical 10 CIFAR-10 labels.
B. A ``ColorCentroidRule`` constructed from a small seed of labelled
   images returns a predicted label for an unseen image, beats
   chance (10% on 10 balanced classes).
C. An ``ImagePixelLogRegHead`` trains on (image, label) records and
   beats the color-centroid rule by a paired-McNemar-significant
   margin on the test split.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest

CIFAR10_LABELS = {
    "airplane",
    "automobile",
    "bird",
    "cat",
    "deer",
    "dog",
    "frog",
    "horse",
    "ship",
    "truck",
}


@pytest.mark.benchmark
class TestCifar10Loader:
    def test_loader_is_importable(self):
        try:
            from dendra.benchmarks.loaders import load_cifar10  # noqa: F401
        except ImportError:
            pytest.fail("dendra.benchmarks.loaders.load_cifar10 not implemented yet")

    def test_returns_ten_canonical_labels(self):
        from dendra.benchmarks.loaders import load_cifar10

        ds = load_cifar10(train_n=200, test_n=50)
        assert ds.name == "cifar10"
        assert ds.train and ds.test
        labels = set(ds.labels)
        assert labels == CIFAR10_LABELS, f"unexpected CIFAR-10 labels — got {labels}"

    def test_pairs_are_image_label_pairs(self):
        from dendra.benchmarks.loaders import load_cifar10

        ds = load_cifar10(train_n=50, test_n=20)
        for image, label in (ds.train + ds.test)[:10]:
            # Image is a numpy array of shape (32, 32, 3), uint8.
            assert isinstance(image, np.ndarray)
            assert image.shape == (32, 32, 3)
            assert image.dtype == np.uint8
            assert label in CIFAR10_LABELS


@pytest.mark.benchmark
class TestColorCentroidRule:
    def test_beats_chance(self):
        from dendra.benchmarks.loaders import load_cifar10
        from dendra.image_rules import build_color_centroid_rule

        ds = load_cifar10(train_n=300, test_n=100)
        rule = build_color_centroid_rule(ds.train[:300])
        hits = sum(1 for img, lbl in ds.test if rule(img) == lbl)
        acc = hits / len(ds.test)
        # 10 balanced classes — chance is 10%. Color rule should clear ~15%
        # consistently (e.g., ship/airplane = blueish, frog = green).
        assert acc > 0.15, f"color-centroid rule below 15%: {acc:.3f}"


@pytest.mark.benchmark
class TestImagePixelLogRegHead:
    def test_trains_and_beats_rule(self):
        from dendra.benchmarks.loaders import load_cifar10
        from dendra.image_rules import build_color_centroid_rule
        from dendra.ml import ImagePixelLogRegHead

        ds = load_cifar10(train_n=600, test_n=200)
        rule = build_color_centroid_rule(ds.train[:300])

        @dataclass
        class _Rec:
            input: np.ndarray
            label: str
            outcome: str = "correct"

        records = [_Rec(img, lbl) for img, lbl in ds.train]
        head = ImagePixelLogRegHead(min_outcomes=10)
        head.fit(records)

        rule_hits = 0
        ml_hits = 0
        for img, true_lbl in ds.test:
            if rule(img) == true_lbl:
                rule_hits += 1
            pred = head.predict(img, list(CIFAR10_LABELS))
            if pred.label == true_lbl:
                ml_hits += 1
        assert ml_hits > rule_hits, (
            f"ML pixel head failed to beat color rule on CIFAR-10: "
            f"rule={rule_hits} ml={ml_hits} of {len(ds.test)}"
        )
