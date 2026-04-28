# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

from dendra import Switch


class RouteSwitch(Switch):

    def _evidence_input(self, req) -> object:
        return req

    def _rule(self, evidence) -> str:
        req = evidence.input
        if req.method == 'POST':
            return 'write'
        elif req.method == 'PUT':
            return 'update'
        if req.path.startswith('/admin'):
            return 'admin'
        return 'read'

    def _on_admin(self, req):
        audit_admin(req)
