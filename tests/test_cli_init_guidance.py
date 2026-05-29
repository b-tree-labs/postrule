# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: LicenseRef-BSL-1.1
#
# After `postrule init` instruments a switch, the CLI must give an
# auth-aware "connect your account" next step — the local→account bridge
# that turns a freshly-instrumented switch into dashboard verdicts.

from postrule.cli import init_connect_guidance


class TestInitConnectGuidance:
    def test_not_connected_shows_the_one_connect_command(self):
        msg = init_connect_guidance(
            False, email=None, switch_name="triage", dashboard_url="https://app.postrule.ai"
        )
        # the one obvious command, the switch it's for, and the CI fallback
        assert "postrule login" in msg
        assert "triage" in msg
        assert "POSTRULE_API_KEY" in msg

    def test_connected_points_at_the_dashboard(self):
        msg = init_connect_guidance(
            True,
            email="ben@example.com",
            switch_name="triage",
            dashboard_url="https://app.postrule.ai",
        )
        assert "ben@example.com" in msg
        assert "https://app.postrule.ai/dashboard" in msg
        assert "triage" in msg
        # don't re-prompt a connected user to log in
        assert "postrule login" not in msg

    def test_connected_without_email_has_no_dangling_none(self):
        msg = init_connect_guidance(
            True, email=None, switch_name="triage", dashboard_url="https://app.postrule.ai"
        )
        assert "None" not in msg
        assert "https://app.postrule.ai/dashboard" in msg
