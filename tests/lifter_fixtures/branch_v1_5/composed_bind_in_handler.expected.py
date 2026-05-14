# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

from postrule import Switch


class HandleSwitch(Switch):

    def _evidence_input(self, record) -> object:
        return record

    def _evidence_text(self, record) -> object:
        return record.body.lower()

    def _rule(self, evidence) -> str:
        record = evidence.input
        if 'urgent' in evidence.text:
            return 'urgent'
        if 'spam' in evidence.text:
            return 'spam'
        return 'normal'

    def _on_urgent(self, record):
        text = record.body.lower()
        emit_alert(text)
