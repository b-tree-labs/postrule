# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

from dendra import Switch


class ChainSwitch(Switch):

    def _evidence_req(self, req) -> object:
        return req

    def _evidence_first_ok(self, req) -> object:
        return first_check(req)

    def _evidence_second_ok(self, req) -> object:
        if self._evidence_first_ok(req):
            return None
        return second_check(req)

    def _evidence_third_ok(self, req) -> object:
        if self._evidence_first_ok(req):
            return None
        if self._evidence_second_ok(req):
            return None
        return third_check(req)

    def _rule(self, evidence) -> str:
        if evidence.first_ok or evidence.second_ok or evidence.third_ok:
            return 'yes'
        return 'no'
