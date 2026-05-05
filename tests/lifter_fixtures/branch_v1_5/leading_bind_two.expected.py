# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

from dendra import Switch


class ClassifySwitch(Switch):

    def _evidence_input(self, text) -> object:
        return text

    def _evidence_lower(self, text) -> object:
        return text.lower()

    def _evidence_length(self, text) -> object:
        lower = text.lower()
        return len(lower)

    def _rule(self, evidence) -> str:
        text = evidence.input
        if evidence.length > 200:
            return 'long'
        elif 'urgent' in evidence.lower:
            return 'urgent'
        else:
            return 'short'
