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

"""Tests for ``postrule.cloud.team_corpus`` — share / fetch team corpora.

Mocks the requests layer to keep tests offline. The HTTP shape is
asserted (URL, method, JSON body, Authorization header) so the
client stays in sync with the api Worker contract.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from postrule import auth
from postrule.cloud import NotLoggedInError, team_corpus


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


@pytest.fixture()
def logged_in(fake_home, monkeypatch):
    """Pretend the user has run ``postrule login`` — write a creds file."""
    monkeypatch.setenv("POSTRULE_CLOUD_API_BASE", "https://api.example.test/v1")
    auth.save_credentials("prul_live_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "user@example.com")
    yield


class TestShareCorpus:
    def test_requires_login(self, fake_home):
        with pytest.raises(NotLoggedInError):
            team_corpus.share_corpus({"rule": {}, "examples": []}, team_id="acme")

    def test_posts_team_id_and_corpus_to_correct_url(self, logged_in):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "share_url": "https://api.example.test/v1/team-corpus/acme-eng"
        }

        with patch("postrule.cloud.team_corpus.requests.post", return_value=mock_resp) as post:
            url = team_corpus.share_corpus({"rule": {"v": 1}}, team_id="acme-eng")

        assert url == "https://api.example.test/v1/team-corpus/acme-eng"
        post.assert_called_once()
        call = post.call_args
        assert call.args[0] == "https://api.example.test/v1/team-corpus"
        # Body wraps the corpus and carries the team_id at top level.
        assert call.kwargs["json"] == {"team_id": "acme-eng", "corpus": {"rule": {"v": 1}}}
        # Authorization header carries the bearer token from credentials.
        assert call.kwargs["headers"]["Authorization"].startswith("Bearer prul_live_")

    def test_synthesizes_url_on_non_ok_response(self, logged_in):
        mock_resp = MagicMock()
        mock_resp.ok = False

        with patch("postrule.cloud.team_corpus.requests.post", return_value=mock_resp):
            url = team_corpus.share_corpus({"rule": {}}, team_id="acme")

        assert url == "https://api.example.test/v1/team-corpus/acme"

    def test_synthesizes_url_on_unparseable_body(self, logged_in):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.side_effect = ValueError("not json")

        with patch("postrule.cloud.team_corpus.requests.post", return_value=mock_resp):
            url = team_corpus.share_corpus({"rule": {}}, team_id="acme")

        assert url == "https://api.example.test/v1/team-corpus/acme"

    def test_uses_env_override_for_base_url(self, logged_in, monkeypatch):
        monkeypatch.setenv("POSTRULE_CLOUD_API_BASE", "http://localhost:8787/v1")
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"share_url": "http://localhost:8787/v1/team-corpus/dev"}

        with patch("postrule.cloud.team_corpus.requests.post", return_value=mock_resp) as post:
            team_corpus.share_corpus({"rule": {}}, team_id="dev")

        assert post.call_args.args[0] == "http://localhost:8787/v1/team-corpus"


class TestFetchTeamCorpus:
    def test_requires_login(self, fake_home):
        with pytest.raises(NotLoggedInError):
            team_corpus.fetch_team_corpus("acme")

    def test_returns_payload_on_ok(self, logged_in):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"rule": {"v": 1}, "examples": []}

        with patch("postrule.cloud.team_corpus.requests.get", return_value=mock_resp) as get:
            result = team_corpus.fetch_team_corpus("acme")

        assert result == {"rule": {"v": 1}, "examples": []}
        assert get.call_args.args[0] == "https://api.example.test/v1/team-corpus/acme"

    def test_returns_empty_on_404(self, logged_in):
        mock_resp = MagicMock()
        mock_resp.ok = False

        with patch("postrule.cloud.team_corpus.requests.get", return_value=mock_resp):
            assert team_corpus.fetch_team_corpus("missing") == {}

    def test_returns_empty_on_non_dict_body(self, logged_in):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = ["unexpected", "shape"]

        with patch("postrule.cloud.team_corpus.requests.get", return_value=mock_resp):
            assert team_corpus.fetch_team_corpus("weird") == {}

    def test_returns_empty_on_unparseable_body(self, logged_in):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.side_effect = ValueError("not json")

        with patch("postrule.cloud.team_corpus.requests.get", return_value=mock_resp):
            assert team_corpus.fetch_team_corpus("any") == {}
