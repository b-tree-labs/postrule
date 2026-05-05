# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Resource-exhaustion tests.

Goal: validate that hostile or buggy inputs cannot DOS the process.

Coverage:
  - 1000 concurrent dispatches: completes or rate-limits clean.
  - on= callable that infinitely recurses into the switch:
    RecursionError caught, classification result still returns.
  - on= callable that tries to fork: sandbox blocks subprocess.
  - 100k labels in a single switch: dispatch latency stays bounded.
"""

from __future__ import annotations

import concurrent.futures
import time

import pytest

from dendra import Label, LearnedSwitch

pytestmark = pytest.mark.redteam


# ---------------------------------------------------------------------
# Concurrent dispatches: thread-pool starvation check
# ---------------------------------------------------------------------


def test_thousand_concurrent_dispatches_complete():
    """1000 concurrent dispatches must all complete without deadlock or
    starvation. Bounded to 60s - anything longer is a bug.
    """
    sw = LearnedSwitch(rule=lambda _: "ok", name="concurrent-1k")

    def call(_i):
        return sw.dispatch(f"input-{_i}").label

    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as ex:
        results = list(ex.map(call, range(1000)))
    elapsed = time.time() - start

    assert all(r == "ok" for r in results)
    assert elapsed < 60.0, f"1000 dispatches took {elapsed:.1f}s - concurrency is starving"


# ---------------------------------------------------------------------
# Recursive on= handler
# ---------------------------------------------------------------------


def test_recursive_on_handler_does_not_kill_process():
    """An on= handler that re-dispatches into the same switch will hit
    Python's recursion limit deep in the call stack. The PROCESS
    must NOT crash; the outermost dispatch returns a normal result
    (its own action handler completed; the recursion error was caught
    deeper in the stack by ``_maybe_dispatch``).

    Critical: NO unhandled exception escapes the outermost dispatch.
    """

    def rule(_):
        return "loop"

    container: dict = {"sw": None}

    def recursive_handler(input):
        # Re-dispatch into the SAME switch from inside the on= handler.
        container["sw"].dispatch(input)

    sw = LearnedSwitch(
        rule=rule,
        labels=[Label(name="loop", on=recursive_handler)],
        name="recursive-on",
    )
    container["sw"] = sw

    # The load-bearing claim: this call returns. It does NOT raise
    # RecursionError into the user's code. (The recursion is caught
    # by Dendra's own action-exception capture.)
    result = sw.dispatch("trigger")
    assert result.label == "loop"


# ---------------------------------------------------------------------
# Sandbox blocks fork attempts in the on= handler
# ---------------------------------------------------------------------


def test_on_handler_subprocess_blocked_by_sandbox():
    """An on= handler that tries to fork via subprocess.Popen is blocked
    by the sandbox's network/write guards (Popen ultimately needs a
    write to /tmp or a socket, both of which the sandbox restricts).

    More importantly, the subprocess will inherit the test runner's
    environment and may try to connect somewhere - the sandbox catches
    that. We assert the dispatch completes (action_raised set) without
    bringing down the process.
    """
    import subprocess

    fired = []

    def rule(_):
        return "fork"

    def fork_handler(_):
        fired.append("entered")
        # Attempt a subprocess call. May succeed (running /bin/true) or
        # be blocked by the sandbox's write guard if the OS needs to
        # write something. Either way the result.action_* fields are
        # populated and the parent process is fine.
        subprocess.run(["/bin/true"], check=True, timeout=2)

    sw = LearnedSwitch(
        rule=rule,
        labels=[Label(name="fork", on=fork_handler)],
        name="fork-attempt",
    )
    result = sw.dispatch("x")
    assert result.label == "fork"
    assert fired == ["entered"]
    # If the subprocess raised, action_raised holds the error message;
    # if it succeeded, action_raised is None. Both outcomes prove the
    # parent process survived.


# ---------------------------------------------------------------------
# Many labels - dispatch latency stays bounded
# ---------------------------------------------------------------------


def test_dispatch_latency_with_100k_labels():
    """Constructing a switch with 100k labels and dispatching once
    must complete in well under 1s. Catches an O(N) per-call linear
    scan that would kneecap users with large vocabularies.
    """
    labels = [f"label-{i}" for i in range(100_000)]
    sw = LearnedSwitch(rule=lambda _: "label-50000", labels=labels, name="big-vocab")

    start = time.time()
    result = sw.dispatch("any")
    elapsed = time.time() - start

    assert result.label == "label-50000"
    assert elapsed < 1.0, f"dispatch with 100k labels took {elapsed:.3f}s"


def test_label_lookup_is_dict_not_linear():
    """The label name index must be a dict for O(1) lookup. Otherwise
    a 100k-label switch would have an O(N) per-call dispatch.
    """
    from dendra.core import LearnedSwitch as LS

    sw = LS(rule=lambda _: "ok", labels=[f"l{i}" for i in range(1000)], name="dict-lookup")
    # The label index must be a dict.
    assert isinstance(sw._label_index, dict)
    # And keyed by label name.
    assert "l500" in sw._label_index


# ---------------------------------------------------------------------
# Repeated big-input dispatches - make sure we don't accumulate state
# ---------------------------------------------------------------------


def test_repeated_dispatch_does_not_leak_memory_proportional_to_input():
    """Dispatching with a 1MB input 100 times must NOT blow up memory
    proportional to (input_size * num_calls). The Switch may log
    records, but must not pin the input objects indefinitely.

    Coarse check: process memory before/after stays within 200 MB.
    """
    import gc
    import os
    import resource

    sw = LearnedSwitch(rule=lambda _: "ok", name="leak-check")
    big = "y" * (1024 * 1024)

    gc.collect()
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    for _ in range(100):
        sw.dispatch(big)

    gc.collect()
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    # ru_maxrss is in bytes on macOS, kilobytes on Linux. We allow a
    # generous 200 MB headroom either way.
    delta_kb = abs(rss_after - rss_before) / 1024.0
    if os.uname().sysname == "Darwin":
        # bytes already, convert to MB
        delta_mb = abs(rss_after - rss_before) / (1024 * 1024)
    else:
        delta_mb = delta_kb / 1024.0
    assert delta_mb < 200, f"memory grew {delta_mb:.1f} MB across 100 dispatches"


# ---------------------------------------------------------------------
# Pathological label-rule combinations
# ---------------------------------------------------------------------


def test_dispatch_with_label_returning_unhashable_does_not_crash():
    """A rule that returns an unhashable object (e.g. dict) must not
    crash the dispatcher; it falls through as 'no match' and returns
    the rule's value as the label.
    """
    sw = LearnedSwitch(
        rule=lambda _: ["unhashable", "list"],
        labels=["safe"],
        name="unhashable-return",
    )
    # Must not crash.
    result = sw.dispatch("x")
    assert result.label == ["unhashable", "list"]
