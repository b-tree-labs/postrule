# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

from dendra import Switch


class ClassifySwitch(Switch):

    def _evidence_input(self, payload) -> object:
        return payload

    def _evidence_kind(self, payload) -> object:
        return payload.kind.lower()

    def _rule(self, evidence) -> str:
        payload = evidence.input
        if evidence.kind == 'urgent':
            return 'urgent'
        if evidence.kind == 'bug':
            return 'bug'
        return 'other'

    def _on_urgent(self, payload):
        page_oncall(payload)

    def _on_other(self, payload):
        log_unknown(payload)
