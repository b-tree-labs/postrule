# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

def check(self, request):
    if self.cache_state == "warm":
        return "cached"
    return "miss"
