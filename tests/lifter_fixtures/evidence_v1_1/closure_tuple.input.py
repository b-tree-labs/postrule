# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

def make_router(allowed_tags: tuple):
    def route(item):
        if item.tag in allowed_tags:
            return "permitted"
        return "blocked"
    return route
