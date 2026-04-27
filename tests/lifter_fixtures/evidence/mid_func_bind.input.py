# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

def route(text):
    user = db.lookup(text)
    if user.tier == "vip":
        return "vip"
    return "standard"
