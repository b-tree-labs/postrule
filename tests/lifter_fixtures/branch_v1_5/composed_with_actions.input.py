# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

def route(ticket):
    title = ticket.title.lower()
    if 'crash' in title:
        log_bug(ticket)
        return 'bug'
    if 'feature' in title:
        notify_product(ticket)
        return 'feature'
    return 'other'
