# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

from postrule import Switch


class RouteSwitch(Switch):

    def _evidence_text(self, text) -> object:
        return text

    def _evidence_fast_lane(self, text) -> object:
        """Dispatch-time snapshot: re-reads closure `flags` on every call."""
        return flags['fast_lane']

    def _rule(self, evidence) -> str:
        if evidence.fast_lane:
            return 'fast'
        return 'normal'
