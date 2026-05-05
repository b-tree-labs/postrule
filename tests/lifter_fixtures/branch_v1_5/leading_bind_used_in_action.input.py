# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

def label(record):
    text = record.body.lower()
    if 'urgent' in text:
        emit_alert(text)
        return 'urgent'
    else:
        return 'normal'
