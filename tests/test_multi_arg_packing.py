# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Multi-arg / kwargs packing for ``@ml_switch`` and ``dendra.Switch``.

A user's rule function (or a Switch subclass's evidence / rule / on
methods) may take more than one positional argument. The wrapping
machinery introspects the user signature, builds a synthetic
packed-input dataclass from parameter names + annotations, and
unpacks it again before calling user code. The LLM/ML head sees a
single typed object; the user keeps writing idiomatic Python.

Test contracts (each one a single claim).
"""

from __future__ import annotations

from dataclasses import is_dataclass

import pytest

from dendra import Switch, ml_switch

# ----------------------------------------------------------------------
# Decorator path: @ml_switch on multi-arg functions.
# ----------------------------------------------------------------------


class TestMlSwitchDecoratorMultiArg:
    """Multi-arg ``@ml_switch`` rules pack their inputs into a typed object
    behind the scenes; the user's call signature is unchanged.
    """

    def test_two_positional_args_with_type_hints_callable(self):
        @ml_switch(labels=["api", "ui"], author="alice")
        def route(method: str, path: str) -> str:
            return "api" if path.startswith("/api") else "ui"

        # Original call signature unchanged.
        assert route("GET", "/api/users") == "api"
        assert route("GET", "/home") == "ui"

    def test_three_args_classify_returns_label(self):
        @ml_switch(labels=["api", "ui", "admin"], author="alice")
        def route(method: str, path: str, headers: dict) -> str:
            if method == "POST" and path.startswith("/api"):
                return "api"
            if path.startswith("/admin"):
                return "admin"
            return "ui"

        result = route.classify("POST", "/api/users", {"x": "y"})
        assert result.label == "api"

        result = route.classify("GET", "/admin/panel", {})
        assert result.label == "admin"

    def test_three_args_dispatch_fires_on_handler_with_original_args(self):
        tape: list[tuple] = []

        def on_api(method, path, headers):
            tape.append(("api", method, path, headers))
            return "served-api"

        @ml_switch(
            labels={"api": on_api, "ui": None, "admin": None},
            author="alice",
        )
        def route(method: str, path: str, headers: dict) -> str:
            return "api" if path.startswith("/api") else "ui"

        result = route.dispatch("POST", "/api/users", {"x": "y"})
        assert result.label == "api"
        # The on= callable saw the original positional args, not the packed object.
        assert tape == [("api", "POST", "/api/users", {"x": "y"})]
        assert result.action_result == "served-api"

    def test_kwargs_call_packs_correctly(self):
        @ml_switch(labels=["api", "ui"], author="alice")
        def route(method: str, path: str) -> str:
            return "api" if path.startswith("/api") else "ui"

        # Calling the wrapped function with kwargs should also work.
        assert route(method="GET", path="/api/x") == "api"
        # Mixed positional + keyword.
        assert route("POST", path="/home") == "ui"

    def test_default_values_preserved(self):
        @ml_switch(labels=["api", "ui"], author="alice")
        def route(method: str, path: str = "/", headers: dict = None) -> str:
            return "api" if path.startswith("/api") else "ui"

        assert route("GET") == "ui"
        assert route("GET", "/api/users") == "api"
        # classify with one arg + defaults filling the rest.
        assert route.classify("GET").label == "ui"
        assert route.classify("GET", "/api/x").label == "api"

    def test_var_args_and_var_kwargs_packed_as_named_fields(self):
        @ml_switch(labels=["a", "b"], author="alice")
        def f(x: int, *args: int, **kwargs: int) -> str:
            return "a" if x > 0 else "b"

        assert f(1, 2, 3, foo=4) == "a"
        assert f(-1) == "b"
        # Dispatch should also work and the on= handler should receive
        # the original args + kwargs unchanged.
        tape: list[tuple] = []

        def on_a(x, *args, **kwargs):
            tape.append((x, args, kwargs))

        @ml_switch(labels={"a": on_a, "b": None}, author="alice")
        def g(x: int, *args: int, **kwargs: int) -> str:
            return "a"

        g.dispatch(1, 2, 3, foo=4)
        assert tape == [(1, (2, 3), {"foo": 4})]

    def test_missing_annotation_with_multi_arg_raises(self):
        with pytest.raises(TypeError, match=r"annotation"):

            @ml_switch(labels=["a", "b"], author="alice")
            def f(method, path: str) -> str:  # missing annotation on method
                return "a"

    def test_single_arg_without_annotation_still_works(self):
        """Back-compat: single-arg rules can omit annotations."""

        @ml_switch(labels=["a", "b"], author="alice")
        def f(x):
            return "a" if x else "b"

        assert f(True) == "a"
        assert f.classify(False).label == "b"

    def test_packed_evidence_class_has_named_fields(self):
        @ml_switch(labels=["a", "b"], author="alice")
        def f(x: int, y: str) -> str:
            return "a"

        ev_cls = f._packed_signature.packed_class
        assert is_dataclass(ev_cls)
        fields = {fld.name for fld in ev_cls.__dataclass_fields__.values()}
        assert fields == {"x", "y"}


# ----------------------------------------------------------------------
# Switch class path: multi-arg evidence / on / rule methods.
# ----------------------------------------------------------------------


class TestSwitchClassMultiArg:
    """A Switch subclass with multi-arg ``_evidence_*`` methods packs
    them into a single dispatch input automatically.
    """

    def test_multi_arg_evidence_methods_dispatch(self):
        tape: list[tuple] = []

        class RouteRequest(Switch):
            def _evidence_method(self, method: str, path: str, headers: dict) -> str:
                return method

            def _evidence_path_prefix(self, method: str, path: str, headers: dict) -> str:
                return path.split("/")[1] if "/" in path else ""

            def _rule(self, evidence) -> str:
                if evidence.method == "POST" and evidence.path_prefix == "api":
                    return "api"
                if evidence.path_prefix == "admin":
                    return "admin"
                return "ui"

            def _on_api(self, method: str, path: str, headers: dict):
                tape.append(("api", method, path, headers))
                return "served-api"

            def _on_admin(self, method: str, path: str, headers: dict):
                tape.append(("admin", method, path, headers))

            class Meta:
                no_action = ("ui",)

        switch = RouteRequest()

        # classify path
        assert switch.classify("POST", "/api/users", {"k": "v"}).label == "api"
        assert switch.classify("GET", "/admin/panel", {}).label == "admin"
        assert switch.classify("GET", "/home", {}).label == "ui"

        # dispatch path: handler receives original positional args
        result = switch.dispatch("POST", "/api/users", {"k": "v"})
        assert result.label == "api"
        assert tape == [("api", "POST", "/api/users", {"k": "v"})]
        assert result.action_result == "served-api"

    def test_evidence_signature_mismatch_raises(self):
        """If two _evidence_* methods declare different positional signatures
        beyond self, it's a class-definition-time error.
        """
        with pytest.raises(TypeError, match=r"signature"):

            class BrokenSwitch(Switch):
                def _evidence_a(self, x: int, y: int) -> int:
                    return x

                def _evidence_b(self, x: int) -> int:  # missing y
                    return x

                def _rule(self, evidence) -> str:
                    return "a"

                def _on_a(self, x, y):
                    pass

                class Meta:
                    no_action = ("a",)

    def test_missing_annotation_on_extra_arg_raises(self):
        """A multi-arg evidence method with no annotation on one of its
        non-self args is a clear error.
        """
        with pytest.raises(TypeError, match=r"annotation"):

            class BrokenSwitch(Switch):
                def _evidence_method(self, method, path: str) -> str:
                    # `method` lacks an annotation
                    return method

                def _rule(self, evidence) -> str:
                    return "a"

                def _on_a(self, method, path):
                    pass

                class Meta:
                    no_action = ("a",)

    def test_single_arg_evidence_still_works(self):
        """Back-compat: single-positional evidence keeps working unchanged."""

        class Single(Switch):
            def _evidence_x(self, text: str) -> str:
                return text

            def _rule(self, evidence) -> str:
                return "a" if evidence.x else "b"

            def _on_a(self, text):
                pass

            def _on_b(self, text):
                pass

        s = Single()
        assert s.classify("hello").label == "a"
        assert s.classify("").label == "b"

    def test_aclassify_works_with_multi_arg(self):
        import asyncio

        class R(Switch):
            def _evidence_first(self, a: int, b: int) -> int:
                return a

            def _rule(self, evidence) -> str:
                return "pos" if evidence.first > 0 else "nonpos"

            class Meta:
                no_action = ("pos", "nonpos")

        s = R()
        result = asyncio.run(s.aclassify(5, -1))
        assert result.label == "pos"
