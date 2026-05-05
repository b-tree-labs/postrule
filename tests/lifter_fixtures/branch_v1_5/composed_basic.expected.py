# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

from dendra import Switch


class TriageRuleSwitch(Switch):

    def _evidence_input(self, ticket) -> object:
        return ticket

    def _evidence_title(self, ticket) -> object:
        return ticket.get('title', '').lower()

    def _rule(self, evidence) -> str:
        ticket = evidence.input
        if 'crash' in evidence.title or 'error' in evidence.title:
            return 'bug'
        if evidence.title.endswith('?'):
            return 'question'
        return 'feature_request'
