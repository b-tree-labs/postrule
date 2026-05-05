# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

def route(req):
    if req.method == 'POST':
        return 'write'
    elif req.method == 'PUT':
        return 'update'
    if req.path.startswith('/admin'):
        audit_admin(req)
        return 'admin'
    return 'read'
