# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

def gate(text):
    if FEATURE_FLAGS["fast_lane"]:
        return "fast"
    return "slow"
