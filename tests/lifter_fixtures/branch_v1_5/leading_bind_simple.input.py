# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

def triage(ticket):
    title = ticket.get('title', '').lower()
    if 'crash' in title:
        return 'bug'
    elif title.endswith('?'):
        return 'question'
    else:
        return 'feature_request'
