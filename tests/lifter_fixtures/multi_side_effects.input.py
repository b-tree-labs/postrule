# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

def route(req):
    if req.method == 'POST':
        log_request(req)
        emit_metric(req)
        notify_audit(req)
        return 'write'
    else:
        log_request(req)
        return 'read'
