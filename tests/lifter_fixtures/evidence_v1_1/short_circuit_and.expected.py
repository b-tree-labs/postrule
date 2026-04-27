# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

from dendra import Switch


class GateSwitch(Switch):

    def _evidence_user(self, user) -> object:
        return user

    def _evidence_has_ok(self, user) -> object:
        return has_account(user)

    def _evidence_is_ok(self, user) -> object:
        if not self._evidence_has_ok(user):
            return None
        return is_active(user)

    def _rule(self, evidence) -> str:
        if evidence.has_ok and evidence.is_ok:
            return 'ok'
        return 'blocked'
