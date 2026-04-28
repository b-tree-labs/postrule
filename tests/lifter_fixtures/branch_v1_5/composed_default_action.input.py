# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

def classify(payload):
    kind = payload.kind.lower()
    if kind == 'urgent':
        page_oncall(payload)
        return 'urgent'
    if kind == 'bug':
        return 'bug'
    log_unknown(payload)
    return 'other'
