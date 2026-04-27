# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""MLHead extensibility: public base class + factory registry.

Two contracts:

A. ``TfidfHeadBase`` is a public class users can subclass to ship
   their own TF-IDF + sklearn-estimator head. Override
   ``_build_classifier()``; everything else (fit, predict,
   model_version, state_bytes, load_state) is inherited.

B. The MLHead registry (``register_ml_head``, ``make_ml_head``,
   ``available_ml_heads``) allows users to register custom heads
   by name. Built-in heads are pre-registered. Custom strategies
   can refer to heads by name without importing the class.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# A: Public TfidfHeadBase
# ---------------------------------------------------------------------------


class TestTfidfHeadBaseIsPublic:
    def test_can_be_imported_from_dendra(self):
        try:
            from dendra import TfidfHeadBase
        except ImportError:
            pytest.fail("dendra.TfidfHeadBase not exported")
        assert isinstance(TfidfHeadBase, type), "TfidfHeadBase must be a class"

    def test_subclassing_yields_a_working_head(self):
        pytest.importorskip("sklearn")
        from dendra import MLHead, TfidfHeadBase

        class CustomLogRegHead(TfidfHeadBase):
            def _build_classifier(self):
                from sklearn.linear_model import LogisticRegression

                return LogisticRegression(max_iter=500, C=0.1)

        head = CustomLogRegHead(min_outcomes=10)
        assert isinstance(head, MLHead), "subclass must satisfy MLHead protocol"


# ---------------------------------------------------------------------------
# B: MLHead registry
# ---------------------------------------------------------------------------


class TestMLHeadRegistry:
    def test_built_in_heads_pre_registered(self):
        from dendra import available_ml_heads

        names = set(available_ml_heads())
        # Every shipped head must be addressable by name.
        for expected in (
            "tfidf_logreg",
            "tfidf_linearsvc",
            "tfidf_multinomial_nb",
            "tfidf_gradient_boosting",
            "image_pixel_logreg",
        ):
            assert expected in names, (
                f"built-in head {expected!r} not in registry; got {sorted(names)}"
            )

    def test_make_returns_a_working_head(self):
        pytest.importorskip("sklearn")
        from dendra import MLHead, make_ml_head

        head = make_ml_head("tfidf_logreg")
        assert isinstance(head, MLHead)

    def test_unknown_head_raises(self):
        from dendra import make_ml_head

        with pytest.raises(ValueError, match="unknown"):
            make_ml_head("not_a_real_head")

    def test_register_custom_head(self):
        pytest.importorskip("sklearn")
        from dendra import (
            MLHead,
            TfidfHeadBase,
            available_ml_heads,
            make_ml_head,
            register_ml_head,
        )

        class CustomHead(TfidfHeadBase):
            def _build_classifier(self):
                from sklearn.linear_model import LogisticRegression

                return LogisticRegression(max_iter=200)

        # Use a unique name to avoid polluting the registry across tests.
        register_ml_head("test_custom_head_12345", lambda: CustomHead(min_outcomes=10))
        try:
            assert "test_custom_head_12345" in available_ml_heads()
            head = make_ml_head("test_custom_head_12345")
            assert isinstance(head, CustomHead)
            assert isinstance(head, MLHead)
        finally:
            # Clean up so the registry isn't polluted across tests.
            from dendra.ml import _HEAD_REGISTRY  # type: ignore[attr-defined]

            _HEAD_REGISTRY.pop("test_custom_head_12345", None)

    def test_duplicate_registration_raises(self):
        from dendra import register_ml_head

        with pytest.raises(ValueError, match="already registered"):
            register_ml_head("tfidf_logreg", lambda: None)
