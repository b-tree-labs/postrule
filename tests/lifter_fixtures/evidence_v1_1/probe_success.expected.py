# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

from dendra import Switch


class MaybeChargeSwitch(Switch):

    def _evidence_req(self, req) -> object:
        return req

    def _evidence_charge_status(self, req) -> object:
        return api.charge_probe(req)

    def _rule(self, evidence) -> str:
        req = evidence.req
        if evidence.charge_status.ok:
            notify(req)
            return 'charged'
        return 'skipped'
