# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

from dendra import Switch


class GateSwitch(Switch):

    def _evidence_score(self, score) -> object:
        return score

    def _evidence_threshold(self, score, _captured_threshold=threshold) -> object:
        """Decoration-time snapshot: closure `threshold` annotated as Final or a frozen type, captured once."""
        return _captured_threshold

    def _rule(self, evidence) -> str:
        score = evidence.score
        if score > evidence.threshold:
            return 'high'
        return 'low'
