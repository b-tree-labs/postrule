# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

def make_router(flags):
    def route(text):
        if flags["fast_lane"]:
            return "fast"
        return "normal"
    return route
