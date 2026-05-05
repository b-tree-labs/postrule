# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

from dendra import Switch


class CheckSwitch(Switch):

    def _evidence_request(self, request) -> object:
        return request

    def _evidence_cache_state(self, request) -> object:
        return self.cache_state

    def _rule(self, evidence) -> str:
        if evidence.cache_state == 'warm':
            return 'cached'
        return 'miss'
