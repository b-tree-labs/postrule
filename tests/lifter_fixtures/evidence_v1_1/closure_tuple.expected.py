# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

from dendra import Switch


class RouteSwitch(Switch):

    def _evidence_item(self, item) -> object:
        return item

    def _evidence_allowed_tags(self, item, _captured_allowed_tags=allowed_tags) -> object:
        """Decoration-time snapshot: closure `allowed_tags` annotated as Final or a frozen type, captured once."""
        return _captured_allowed_tags

    def _rule(self, evidence) -> str:
        item = evidence.item
        if item.tag in evidence.allowed_tags:
            return 'permitted'
        return 'blocked'
