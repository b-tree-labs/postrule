# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

from dendra import Switch


class RouteSwitch(Switch):

    def _evidence_text(self, text) -> object:
        return text

    def _evidence_user(self, text) -> object:
        return db.lookup(text)

    def _rule(self, evidence) -> str:
        if evidence.user.tier == 'vip':
            return 'vip'
        return 'standard'
