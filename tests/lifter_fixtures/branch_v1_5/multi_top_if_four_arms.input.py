# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

def handling_rule(ctx):
    if ctx.http_status in (502, 503, 504):
        return 'retry'
    if ctx.http_status in (401, 403):
        return 'escalate'
    if ctx.exception_type == 'ValueError':
        return 'drop'
    if ctx.exception_type == 'TimeoutError':
        return 'fallback'
    return 'escalate'
