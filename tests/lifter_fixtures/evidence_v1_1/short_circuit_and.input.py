# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

def gate(user):
    if has_account(user) and is_active(user):
        return "ok"
    return "blocked"
