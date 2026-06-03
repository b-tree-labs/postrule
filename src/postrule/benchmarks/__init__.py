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
from postrule.benchmarks.loaders_audio import load_esc50
from postrule.benchmarks.loaders_ext import (
    ALL_TEXT_EXT_LOADERS,
    load_dbpedia14,
    load_emotion,
    load_imdb,
    load_rotten_tomatoes,
    load_sms_spam,
    load_sst2,
    load_tweet_emotion,
    load_tweet_hate,
    load_tweet_offensive,
    load_twenty_newsgroups,
    load_yahoo_answers,
)

__all__ = [
    "ALL_TEXT_EXT_LOADERS",
    "load_esc50",
    "load_dbpedia14",
    "load_emotion",
    "load_imdb",
    "load_rotten_tomatoes",
    "load_sms_spam",
    "load_sst2",
    "load_tweet_emotion",
    "load_tweet_hate",
    "load_tweet_offensive",
    "load_twenty_newsgroups",
    "load_yahoo_answers",
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
