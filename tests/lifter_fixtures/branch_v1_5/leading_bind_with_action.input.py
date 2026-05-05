# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

def route(ticket):
    title = ticket.title.lower()
    if 'crash' in title:
        log_bug(ticket)
        return 'bug'
    elif 'feature' in title:
        return 'feature'
    else:
        return 'other'
