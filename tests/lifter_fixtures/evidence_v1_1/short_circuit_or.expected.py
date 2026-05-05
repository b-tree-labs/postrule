# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

from dendra import Switch


class AuthorizeSwitch(Switch):

    def _evidence_user(self, user) -> object:
        return user

    def _evidence_cheap_ok(self, user) -> object:
        return cheap_check(user)

    def _evidence_expensive_ok(self, user) -> object:
        if self._evidence_cheap_ok(user):
            return None
        return expensive_db_lookup(user)

    def _rule(self, evidence) -> str:
        if evidence.cheap_ok or evidence.expensive_ok:
            return 'allowed'
        return 'denied'
