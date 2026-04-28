# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

def classify(text):
    lower = text.lower()
    length = len(lower)
    if length > 200:
        return 'long'
    elif 'urgent' in lower:
        return 'urgent'
    else:
        return 'short'
