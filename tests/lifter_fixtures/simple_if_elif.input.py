# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

def triage(ticket):
    if ticket.severity == 'high':
        log_bug(ticket)
        return 'bug'
    elif ticket.kind == 'question':
        notify_support(ticket)
        return 'question'
    else:
        notify_product(ticket)
        return 'feature_request'
