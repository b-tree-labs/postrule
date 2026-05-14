# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

from postrule import Switch


class RouteSwitch(Switch):

    def _evidence_input(self, req) -> object:
        return req

    def _rule(self, evidence) -> str:
        req = evidence.input
        if req.method == 'POST':
            return 'write'
        else:
            return 'read'

    def _on_write(self, req):
        log_request(req)
        emit_metric(req)
        notify_audit(req)

    def _on_read(self, req):
        log_request(req)
