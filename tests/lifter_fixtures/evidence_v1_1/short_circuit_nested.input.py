# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

def chain(req):
    if first_check(req) or second_check(req) or third_check(req):
        return "yes"
    return "no"
