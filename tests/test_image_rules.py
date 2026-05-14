# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Smoke tests for ``postrule.image_rules.build_color_centroid_rule``.

The function is the auto-rule constructor cited in the companion paper
(§5.8, CIFAR-10 image-modality bench). These tests cover the contract
that the rule:

- Returns a callable for any input, including empty (protocol contract:
  rules are always callable).
- Predicts the class whose mean-RGB centroid is L2-nearest to the
  query's mean RGB.
- Treats per-pixel input shape transparently (reshape-(-1, 3) on the
  caller side).
- Is deterministic given the same train pairs.
"""

from __future__ import annotations

import numpy as np
import pytest

from postrule.image_rules import build_color_centroid_rule


def _solid(color: tuple[int, int, int], size: int = 4) -> np.ndarray:
    """Return an HxWx3 image filled with one RGB color."""
    return np.full((size, size, 3), color, dtype=np.uint8)


class TestEmpty:
    def test_empty_input_returns_callable(self) -> None:
        rule = build_color_centroid_rule(iter([]))
        assert callable(rule)

    def test_empty_rule_predicts_empty_string(self) -> None:
        rule = build_color_centroid_rule(iter([]))
        result = rule(_solid((128, 128, 128)))
        assert result == ""


class TestSingleClass:
    def test_single_class_predicts_that_class(self) -> None:
        train = [(_solid((255, 0, 0)), "red"), (_solid((250, 5, 5)), "red")]
        rule = build_color_centroid_rule(train)
        # Any query returns "red" — only one centroid in the model.
        assert rule(_solid((0, 255, 0))) == "red"
        assert rule(_solid((0, 0, 255))) == "red"
        assert rule(_solid((128, 128, 128))) == "red"


class TestTwoClasses:
    def test_predicts_nearest_centroid(self) -> None:
        train = [
            (_solid((255, 0, 0)), "red"),
            (_solid((0, 0, 255)), "blue"),
        ]
        rule = build_color_centroid_rule(train)

        # Pure-red query → red; pure-blue → blue.
        assert rule(_solid((255, 0, 0))) == "red"
        assert rule(_solid((0, 0, 255))) == "blue"

        # Reddish-purple closer to red.
        assert rule(_solid((200, 0, 80))) == "red"
        # Bluish-purple closer to blue.
        assert rule(_solid((80, 0, 200))) == "blue"


class TestMultiClass:
    def test_three_class_rgb_separation(self) -> None:
        train = [
            (_solid((255, 0, 0)), "red"),
            (_solid((0, 255, 0)), "green"),
            (_solid((0, 0, 255)), "blue"),
        ]
        rule = build_color_centroid_rule(train)
        assert rule(_solid((250, 5, 5))) == "red"
        assert rule(_solid((5, 250, 5))) == "green"
        assert rule(_solid((5, 5, 250))) == "blue"

    def test_centroid_averages_seed_pixels(self) -> None:
        # Two "red" seeds, one (255,0,0) and one (245,10,10) → mean (250,5,5).
        # One "blue" seed (0,0,255). Query (200,5,5) is closer to red mean.
        train = [
            (_solid((255, 0, 0)), "red"),
            (_solid((245, 10, 10)), "red"),
            (_solid((0, 0, 255)), "blue"),
        ]
        rule = build_color_centroid_rule(train)
        assert rule(_solid((200, 5, 5))) == "red"


class TestDeterminism:
    def test_same_seeds_yield_same_predictions(self) -> None:
        train = [
            (_solid((255, 0, 0)), "red"),
            (_solid((0, 0, 255)), "blue"),
        ]
        rule_a = build_color_centroid_rule(list(train))
        rule_b = build_color_centroid_rule(list(train))
        for query in [(100, 50, 50), (10, 10, 200), (128, 128, 128)]:
            assert rule_a(_solid(query)) == rule_b(_solid(query))


class TestInputShapes:
    def test_non_square_image(self) -> None:
        # Mean over all pixels must match regardless of HxW.
        rect = np.full((2, 8, 3), (255, 0, 0), dtype=np.uint8)
        train = [(rect, "red"), (_solid((0, 0, 255)), "blue")]
        rule = build_color_centroid_rule(train)
        assert rule(np.full((1, 16, 3), (250, 5, 5), dtype=np.uint8)) == "red"

    def test_float_pixel_values(self) -> None:
        # Centroid math is in float space; uint8 vs float input must agree
        # on identical content.
        red_uint = _solid((255, 0, 0))
        red_float = red_uint.astype(np.float32)
        train = [(red_uint, "red"), (_solid((0, 0, 255)), "blue")]
        rule = build_color_centroid_rule(train)
        assert rule(red_float) == "red"


class TestEdgeCases:
    def test_iterable_consumed_once(self) -> None:
        # build_color_centroid_rule accepts Iterable; a one-shot iterator
        # must still produce a working rule.
        train_iter = iter([(_solid((255, 0, 0)), "red"), (_solid((0, 0, 255)), "blue")])
        rule = build_color_centroid_rule(train_iter)
        assert rule(_solid((250, 5, 5))) == "red"

    def test_single_seed_per_class(self) -> None:
        train = [(_solid((255, 0, 0)), "red"), (_solid((0, 0, 255)), "blue")]
        rule = build_color_centroid_rule(train)
        # Centroid of class with one seed is just that seed's mean color.
        assert rule(_solid((255, 0, 0))) == "red"

    def test_tied_distance_is_resolved_by_first_label(self) -> None:
        # Equidistant query — rule picks via argmin which returns the
        # earliest tied index. Test that behavior is deterministic.
        train = [
            (_solid((255, 0, 0)), "red"),
            (_solid((0, 0, 255)), "blue"),
        ]
        rule = build_color_centroid_rule(train)
        # Equidistant from (255,0,0) and (0,0,255) — the midpoint.
        midpoint = _solid((127, 0, 127))
        result = rule(midpoint)
        assert result in {"red", "blue"}
        # Same input yields same output every call (determinism).
        for _ in range(3):
            assert rule(midpoint) == result


@pytest.mark.parametrize(
    "color,expected_label",
    [
        ((255, 0, 0), "red"),
        ((0, 255, 0), "green"),
        ((0, 0, 255), "blue"),
        ((250, 5, 5), "red"),
        ((5, 250, 5), "green"),
        ((5, 5, 250), "blue"),
    ],
)
def test_parametric_rgb_separation(color: tuple[int, int, int], expected_label: str) -> None:
    train = [
        (_solid((255, 0, 0)), "red"),
        (_solid((0, 255, 0)), "green"),
        (_solid((0, 0, 255)), "blue"),
    ]
    rule = build_color_centroid_rule(train)
    assert rule(_solid(color)) == expected_label
