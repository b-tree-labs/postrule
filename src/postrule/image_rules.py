# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Reference rules for image classification benchmarks.

Companion to ``postrule.benchmarks.rules`` which builds keyword rules
for text. Image rules cannot use TF-IDF; they need pixel-space
heuristics. This module provides one such heuristic
(``build_color_centroid_rule``) used by the CIFAR-10 bench (paper
§5.8).

The lifecycle, gates, and theorem are modality-agnostic. Only the
auto-rule construction differs per modality.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable

import numpy as np


def build_color_centroid_rule(
    train_pairs: Iterable[tuple[np.ndarray, str]],
) -> Callable[[np.ndarray], str]:
    """Build a rule that classifies an image by nearest mean-color centroid.

    For each class, computes the mean RGB triple of the seed images.
    Predicts an unseen image's label as the class whose centroid is
    L2-nearest to the unseen image's mean RGB.

    Cheap, deterministic, no training. The image analog of a keyword
    rule: it picks the most distinctive coarse signal (color) per
    class and classifies by similarity to the seed centroids.
    """
    by_class: dict[str, list[np.ndarray]] = defaultdict(list)
    for img, lbl in train_pairs:
        rgb_mean = img.reshape(-1, 3).mean(axis=0)
        by_class[lbl].append(rgb_mean)

    if not by_class:
        # No seed: return a rule that picks the first label observed
        # in any future call (or "" if untrained); preserves the
        # protocol contract that a rule is always callable.
        def _empty_rule(img: np.ndarray) -> str:
            return ""

        return _empty_rule

    centroids: dict[str, np.ndarray] = {
        lbl: np.mean(np.stack(rgbs), axis=0) for lbl, rgbs in by_class.items()
    }
    labels = list(centroids)
    centroid_matrix = np.stack([centroids[lbl] for lbl in labels])  # (k, 3)

    def _rule(img: np.ndarray) -> str:
        rgb = img.reshape(-1, 3).mean(axis=0)  # (3,)
        d = np.linalg.norm(centroid_matrix - rgb, axis=1)  # (k,)
        return labels[int(d.argmin())]

    return _rule


__all__ = ["build_color_centroid_rule"]
