# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: LicenseRef-BSL-1.1
#
# Licensed under the Business Source License 1.1 (the "License").
# You may not use this file except in compliance with the License.
# You may obtain a copy of the License at LICENSE-BSL in the
# repository root, or at https://mariadb.com/bsl11/.
#
# Change Date:    2030-05-01
# Change License: Apache License, Version 2.0
#
# Additional Use Grant: see LICENSE-BSL. Production use is
# permitted; offering a competing hosted service is not.

"""Tests for ``dendra.cloud.registry`` — anonymize + contribute to the public registry.

The anonymize path is pure data and tested directly. The contribute
path is a thin HTTP wrapper; mocks the requests layer.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from dendra import auth
from dendra.cloud import NotLoggedInError, registry


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


@pytest.fixture()
def logged_in(fake_home, monkeypatch):
    monkeypatch.setenv("DENDRA_CLOUD_API_BASE", "https://api.example.test/v1")
    auth.save_credentials("dndr_live_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "user@example.com")
    yield


class TestAnonymize:
    def test_strips_top_level_identifying_keys(self):
        corpus = {
            "author": "alice",
            "email": "alice@example.com",
            "owner": "alice",
            "rule": {"v": 1},
        }
        scrubbed = registry.anonymize(corpus)
        assert "author" not in scrubbed
        assert "email" not in scrubbed
        assert "owner" not in scrubbed
        assert scrubbed == {"rule": {"v": 1}}

    def test_strips_nested_identifying_keys(self):
        corpus = {
            "rule": {"v": 1, "author": "alice"},
            "metadata": {"host": "alice-laptop", "label_count": 7},
        }
        scrubbed = registry.anonymize(corpus)
        assert scrubbed == {"rule": {"v": 1}, "metadata": {"label_count": 7}}

    def test_strips_keys_inside_lists(self):
        corpus = {
            "examples": [
                {"text": "hi", "user": "alice"},
                {"text": "bye", "username": "bob"},
            ]
        }
        scrubbed = registry.anonymize(corpus)
        assert scrubbed == {"examples": [{"text": "hi"}, {"text": "bye"}]}

    def test_does_not_strip_non_identifying_keys(self):
        corpus = {
            "rule": {"v": 1},
            "examples": [{"text": "hello"}],
            "label_distribution": {"a": 0.5, "b": 0.5},
        }
        scrubbed = registry.anonymize(corpus)
        assert scrubbed == corpus

    def test_does_not_mutate_input(self):
        corpus = {"author": "alice", "rule": {"v": 1, "owner": "bob"}}
        registry.anonymize(corpus)
        # Original still has identifying keys.
        assert corpus["author"] == "alice"
        assert corpus["rule"]["owner"] == "bob"

    def test_strips_all_known_identifying_keys(self):
        # Every key in the conservative-strip list must be removed.
        all_id_keys = {
            "author": "x",
            "email": "x@x",
            "user": "x",
            "username": "x",
            "owner": "x",
            "repo_url": "x",
            "remote_url": "x",
            "absolute_path": "x",
            "abs_path": "x",
            "host": "x",
            "hostname": "x",
            "machine_id": "x",
        }
        scrubbed = registry.anonymize(all_id_keys)
        assert scrubbed == {}


class TestContribute:
    def test_requires_login(self, fake_home):
        with pytest.raises(NotLoggedInError):
            registry.contribute_anonymized({"rule": {"v": 1}})

    def test_posts_to_correct_url(self, logged_in):
        mock_resp = MagicMock()
        mock_resp.ok = True

        with patch("dendra.cloud.registry.requests.post", return_value=mock_resp) as post:
            ok = registry.contribute_anonymized({"rule": {"v": 1}})

        assert ok is True
        assert post.call_args.args[0] == "https://api.example.test/v1/registry/contribute"
        assert post.call_args.kwargs["headers"]["Authorization"].startswith("Bearer dndr_live_")

    def test_anonymizes_before_upload(self, logged_in):
        # contribute_anonymized must run anonymize() on the input before
        # POSTing — otherwise identifying keys leave the machine.
        mock_resp = MagicMock()
        mock_resp.ok = True
        corpus_with_id = {"author": "alice", "rule": {"v": 1}}

        with patch("dendra.cloud.registry.requests.post", return_value=mock_resp) as post:
            registry.contribute_anonymized(corpus_with_id)

        sent = post.call_args.kwargs["json"]
        assert "author" not in sent
        assert sent == {"rule": {"v": 1}}

    def test_returns_false_on_non_ok_response(self, logged_in):
        mock_resp = MagicMock()
        mock_resp.ok = False

        with patch("dendra.cloud.registry.requests.post", return_value=mock_resp):
            assert registry.contribute_anonymized({"rule": {"v": 1}}) is False

    def test_uses_env_override_for_base_url(self, logged_in, monkeypatch):
        monkeypatch.setenv("DENDRA_CLOUD_API_BASE", "http://localhost:8787/v1")
        mock_resp = MagicMock()
        mock_resp.ok = True

        with patch("dendra.cloud.registry.requests.post", return_value=mock_resp) as post:
            registry.contribute_anonymized({"rule": {"v": 1}})

        assert post.call_args.args[0] == "http://localhost:8787/v1/registry/contribute"
