# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

from postrule import Switch


class TriageSwitch(Switch):

    def _evidence_input(self, ticket) -> object:
        return ticket

    def _rule(self, evidence) -> str:
        ticket = evidence.input
        if ticket.severity == 'high':
            return 'bug'
        elif ticket.kind == 'question':
            return 'question'
        else:
            return 'feature_request'

    def _on_bug(self, ticket):
        log_bug(ticket)

    def _on_question(self, ticket):
        notify_support(ticket)

    def _on_feature_request(self, ticket):
        notify_product(ticket)
