# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

from dendra import Switch


class ClassifySwitch(Switch):

    def _evidence_text(self, text) -> object:
        return text

    def _rule(self, evidence) -> str:
        text = evidence.text
        if text == 'hello':
            return 'greeting'
        return 'other'
