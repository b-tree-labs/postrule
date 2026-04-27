# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

from dataclasses import dataclass

from dendra import Switch


@dataclass
class _RouteRequestArgs:
    method: object
    path: object
    headers: object

class RouteRequestSwitch(Switch):

    def _evidence_input(self, packed) -> object:
        return packed

    def _rule(self, evidence) -> str:
        method = evidence.input.method
        path = evidence.input.path
        headers = evidence.input.headers
        if method == 'POST' and path.startswith('/api'):
            return 'api'
        elif path.startswith('/admin'):
            return 'admin'
        else:
            return 'ui'

    def _on_api(self, packed):
        method = packed.method
        path = packed.path
        headers = packed.headers
        record_api_call(method, path)
