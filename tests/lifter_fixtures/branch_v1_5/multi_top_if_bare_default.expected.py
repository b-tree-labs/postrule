# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

from dendra import Switch


class ClassifySwitch(Switch):

    def _evidence_input(self, req) -> object:
        return req

    def _rule(self, evidence) -> str:
        req = evidence.input
        if req.method == 'POST':
            return 'write'
        if req.method == 'DELETE':
            return 'delete'
        return 'read'

    def _on_write(self, req):
        log_write(req)
