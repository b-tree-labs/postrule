# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Public intent-classification benchmark loaders + close-the-loop harness.

The loaders (``load_banking77`` and friends) require the optional
``datasets`` dependency (``pip install postrule[bench]``). The harness
(``generate_benchmark_module``, ``run_benchmark``, ``aggregate_report``,
``format_report``) has no extra dependencies.
"""

from postrule.benchmarks.harness import (
    GraduationEvent,
    Report,
    SwitchTimeseries,
    aggregate_report,
    format_report,
    generate_benchmark_module,
    run_benchmark,
    run_benchmark_pytest,
)
from postrule.benchmarks.loaders import (
    BenchmarkDataset,
    load_ag_news,
    load_atis,
    load_banking77,
    load_cifar10,
    load_clinc150,
    load_codelangs,
    load_hwu64,
    load_snips,
    load_trec6,
)

__all__ = [
    "BenchmarkDataset",
    "GraduationEvent",
    "Report",
    "SwitchTimeseries",
    "aggregate_report",
    "format_report",
    "generate_benchmark_module",
    "load_ag_news",
    "load_atis",
    "load_banking77",
    "load_cifar10",
    "load_clinc150",
    "load_codelangs",
    "load_hwu64",
    "load_snips",
    "load_trec6",
    "run_benchmark",
    "run_benchmark_pytest",
]
