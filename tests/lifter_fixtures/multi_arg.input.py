# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

def route_request(method, path, headers):
    if method == 'POST' and path.startswith('/api'):
        record_api_call(method, path)
        return 'api'
    elif path.startswith('/admin'):
        return 'admin'
    else:
        return 'ui'
