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

"""Tests for ``dendra.cloud.sync`` — switch-config push / pull stubs."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from dendra import auth
from dendra.cloud import NotLoggedInError, sync


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("DENDRA_API_KEY", raising=False)
    return tmp_path


@pytest.fixture()
def logged_in(fake_home):
    auth.save_credentials("dndra_token_abc", email="u@x.com")
    return fake_home


class TestPushSwitchConfig:
    def test_raises_when_not_logged_in(self, fake_home):
        with pytest.raises(NotLoggedInError):
            sync.push_switch_config({"name": "triage", "phase": "RULE"})

    def test_sends_auth_header_when_logged_in(self, logged_in):
        with patch("dendra.cloud.sync.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.ok = True
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            ok = sync.push_switch_config({"name": "triage", "phase": "RULE"})

            assert ok is True
            assert mock_post.called
            _, kwargs = mock_post.call_args
            headers = kwargs.get("headers", {})
            assert headers.get("Authorization") == "Bearer dndra_token_abc"

    def test_returns_false_on_http_error(self, logged_in):
        with patch("dendra.cloud.sync.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.ok = False
            mock_response.status_code = 500
            mock_post.return_value = mock_response

            ok = sync.push_switch_config({"name": "triage"})
            assert ok is False


class TestPullSwitchConfig:
    def test_raises_when_not_logged_in(self, fake_home):
        with pytest.raises(NotLoggedInError):
            sync.pull_switch_config("triage")

    def test_returns_config_when_found(self, logged_in):
        with patch("dendra.cloud.sync.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.ok = True
            mock_response.status_code = 200
            mock_response.json.return_value = {"name": "triage", "phase": "ML_PRIMARY"}
            mock_get.return_value = mock_response

            config = sync.pull_switch_config("triage")
            assert config == {"name": "triage", "phase": "ML_PRIMARY"}

    def test_returns_none_when_404(self, logged_in):
        with patch("dendra.cloud.sync.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.ok = False
            mock_response.status_code = 404
            mock_get.return_value = mock_response

            assert sync.pull_switch_config("nope") is None

    def test_auth_header_set(self, logged_in):
        with patch("dendra.cloud.sync.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.ok = True
            mock_response.status_code = 200
            mock_response.json.return_value = {"name": "triage"}
            mock_get.return_value = mock_response

            sync.pull_switch_config("triage")
            _, kwargs = mock_get.call_args
            headers = kwargs.get("headers", {})
            assert headers.get("Authorization") == "Bearer dndra_token_abc"
