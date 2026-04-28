# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

def classify(req):
    if req.method == 'POST':
        log_write(req)
        return 'write'
    if req.method == 'DELETE':
        return 'delete'
    return 'read'
