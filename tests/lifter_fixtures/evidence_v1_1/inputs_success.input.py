# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

@evidence_inputs(handler_priority=lambda self, text, kind: getattr(self, f"handle_{kind}").priority)
def route(self, text: str, kind: str):
    if getattr(self, f"handle_{kind}").priority > 5:
        return "high"
    return "low"
