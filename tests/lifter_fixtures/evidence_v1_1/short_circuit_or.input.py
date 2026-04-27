# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

def authorize(user):
    if cheap_check(user) or expensive_db_lookup(user):
        return "allowed"
    return "denied"
