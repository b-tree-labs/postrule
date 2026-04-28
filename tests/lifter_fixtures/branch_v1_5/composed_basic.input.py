# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

def triage_rule(ticket):
    title = ticket.get('title', '').lower()
    if 'crash' in title or 'error' in title:
        return 'bug'
    if title.endswith('?'):
        return 'question'
    return 'feature_request'
