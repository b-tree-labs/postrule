# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

from typing import Final


def make_gate(threshold: Final[int]):
    def gate(score):
        if score > threshold:
            return "high"
        return "low"
    return gate
