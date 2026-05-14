# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

from postrule import Switch


class TriageSwitch(Switch):

    def _evidence_input(self, ticket) -> object:
        return ticket

    def _evidence_title(self, ticket) -> object:
        return ticket.get('title', '').lower()

    def _rule(self, evidence) -> str:
        ticket = evidence.input
        if 'crash' in evidence.title:
            return 'bug'
        elif evidence.title.endswith('?'):
            return 'question'
        else:
            return 'feature_request'
