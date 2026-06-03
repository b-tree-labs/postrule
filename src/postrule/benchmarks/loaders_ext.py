# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Extended text-classification benchmark loaders (DMLR expansion).

These broaden the suite beyond the intent-classification-heavy original
set so the transition-curve study spans *task families* rather than one
task measured several ways. Each loader returns the same
:class:`~postrule.benchmarks.loaders.BenchmarkDataset` shape as the core
loaders and plugs into the same harness untouched.

Families covered here:
  * sentiment / polarity     — sst2, imdb, rotten_tomatoes
  * content moderation       — tweet_eval (offensive, hate)
  * emotion                  — dair-ai/emotion, tweet_eval (emotion)
  * topic / long-document    — dbpedia_14, yahoo_answers_topics, 20news
  * spam                     — sms_spam

The optional ``datasets`` (HuggingFace) dependency is imported lazily via
the shared ``_load`` helper; install with ``pip install postrule[bench]``.
"""

from __future__ import annotations

from random import Random

from postrule.benchmarks.loaders import (
    BenchmarkDataset,
    _collect_string_labels,
    _load,
    _maybe_names,
    _rows_to_pairs,
)


def _split_or_carve(
    pairs: list[tuple[str, str]], *, test_frac: float = 0.2, seed: int = 20260603
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Deterministic train/test carve for corpora that ship train-only."""
    rng = Random(seed)
    shuffled = list(pairs)
    rng.shuffle(shuffled)
    cut = max(1, int((1.0 - test_frac) * len(shuffled)))
    return shuffled[:cut], shuffled[cut:]


# --------------------------------------------------------------------------
# Sentiment / polarity
# --------------------------------------------------------------------------
def load_sst2() -> BenchmarkDataset:
    """SST-2 (Socher et al. 2013) — binary movie-review sentiment.

    GLUE's ``sst2`` test split is unlabeled (label = -1), so we use the
    labeled validation split as the test set (standard practice).
    """
    ds = _load("stanfordnlp/sst2")
    names = _maybe_names(ds["train"].features["label"]) or ["negative", "positive"]
    train = _rows_to_pairs(ds["train"], "sentence", "label", names)
    test = _rows_to_pairs(ds["validation"], "sentence", "label", names)
    return BenchmarkDataset(
        name="sst2",
        train=train,
        test=test,
        labels=names,
        citation="Socher et al. 2013, 'Recursive Deep Models for Semantic Compositionality'",
    )


def load_imdb() -> BenchmarkDataset:
    """IMDB (Maas et al. 2011) — binary long-form movie-review sentiment."""
    ds = _load("imdb")
    names = _maybe_names(ds["train"].features["label"]) or ["neg", "pos"]
    train = _rows_to_pairs(ds["train"], "text", "label", names)
    test = _rows_to_pairs(ds["test"], "text", "label", names)
    return BenchmarkDataset(
        name="imdb",
        train=train,
        test=test,
        labels=names,
        citation="Maas et al. 2011, 'Learning Word Vectors for Sentiment Analysis'",
    )


def load_rotten_tomatoes() -> BenchmarkDataset:
    """Rotten Tomatoes (Pang & Lee 2005) — short-snippet binary sentiment."""
    ds = _load("rotten_tomatoes")
    names = _maybe_names(ds["train"].features["label"]) or ["neg", "pos"]
    train = _rows_to_pairs(ds["train"], "text", "label", names)
    test = _rows_to_pairs(ds["test"], "text", "label", names)
    return BenchmarkDataset(
        name="rotten_tomatoes",
        train=train,
        test=test,
        labels=names,
        citation=(
            "Pang & Lee 2005, 'Seeing Stars: Exploiting Class Relationships "
            "for Sentiment Categorization'"
        ),
    )


# --------------------------------------------------------------------------
# Content moderation
# --------------------------------------------------------------------------
def _load_tweet_eval(config: str, default_names: list[str]) -> BenchmarkDataset:
    ds = _load("tweet_eval", config)
    names = _maybe_names(ds["train"].features["label"]) or default_names
    train = _rows_to_pairs(ds["train"], "text", "label", names)
    test_split = ds["test"] if "test" in ds else ds["validation"]
    test = _rows_to_pairs(test_split, "text", "label", names)
    return BenchmarkDataset(
        name=f"tweet_eval_{config}",
        train=train,
        test=test,
        labels=names or _collect_string_labels(train + test),
        citation="Barbieri et al. 2020, 'TweetEval: Unified Benchmark for Tweet Classification'",
    )


def load_tweet_offensive() -> BenchmarkDataset:
    """TweetEval offensive — binary offensive-language detection (moderation)."""
    return _load_tweet_eval("offensive", ["non-offensive", "offensive"])


def load_tweet_hate() -> BenchmarkDataset:
    """TweetEval hate — binary hate-speech detection (moderation)."""
    return _load_tweet_eval("hate", ["non-hate", "hate"])


# --------------------------------------------------------------------------
# Emotion
# --------------------------------------------------------------------------
def load_emotion() -> BenchmarkDataset:
    """Emotion (Saravia et al. 2018) — 6 Twitter emotion classes."""
    ds = _load("dair-ai/emotion")
    names = _maybe_names(ds["train"].features["label"]) or [
        "sadness",
        "joy",
        "love",
        "anger",
        "fear",
        "surprise",
    ]
    train = _rows_to_pairs(ds["train"], "text", "label", names)
    test_split = ds["test"] if "test" in ds else ds["validation"]
    test = _rows_to_pairs(test_split, "text", "label", names)
    return BenchmarkDataset(
        name="emotion",
        train=train,
        test=test,
        labels=names,
        citation=(
            "Saravia et al. 2018, 'CARER: Contextualized Affect "
            "Representations for Emotion Recognition'"
        ),
    )


def load_tweet_emotion() -> BenchmarkDataset:
    """TweetEval emotion — 4-class emotion (anger, joy, optimism, sadness)."""
    return _load_tweet_eval("emotion", ["anger", "joy", "optimism", "sadness"])


# --------------------------------------------------------------------------
# Topic / long-document
# --------------------------------------------------------------------------
def load_dbpedia14() -> BenchmarkDataset:
    """DBpedia-14 (Zhang et al. 2015) — 14 ontology classes, long text.

    The HF train split is sorted by class, so a capped training prefix
    would see only one label. Shuffle deterministically so any prefix is
    class-balanced (the rule builder shuffles its own seed window
    separately).
    """
    ds = _load("dbpedia_14")
    names = _maybe_names(ds["train"].features["label"])
    train = _rows_to_pairs(ds["train"], "content", "label", names)
    test = _rows_to_pairs(ds["test"], "content", "label", names)
    Random(20260603).shuffle(train)
    return BenchmarkDataset(
        name="dbpedia14",
        train=train,
        test=test,
        labels=names or _collect_string_labels(train + test),
        citation=(
            "Zhang, Zhao & LeCun 2015, 'Character-level Convolutional "
            "Networks for Text Classification'"
        ),
    )


def load_yahoo_answers() -> BenchmarkDataset:
    """Yahoo Answers (Zhang et al. 2015) — 10 topic classes, long text."""
    ds = _load("yahoo_answers_topics")
    label_key = "topic"
    names = _maybe_names(ds["train"].features[label_key])

    def _qa_pairs(split):
        out: list[tuple[str, str]] = []
        for row in split:
            text = " ".join(
                str(row.get(k, "") or "")
                for k in ("question_title", "question_content", "best_answer")
            ).strip()
            raw = row[label_key]
            label = names[raw] if isinstance(raw, int) and names else str(raw)
            if text:
                out.append((text, label))
        return out

    train = _qa_pairs(ds["train"])
    test = _qa_pairs(ds["test"])
    return BenchmarkDataset(
        name="yahoo_answers",
        train=train,
        test=test,
        labels=names or _collect_string_labels(train + test),
        citation=(
            "Zhang, Zhao & LeCun 2015, 'Character-level Convolutional "
            "Networks for Text Classification'"
        ),
    )


def load_twenty_newsgroups() -> BenchmarkDataset:
    """20 Newsgroups (Lang 1995) — 20 topic classes, long multi-paragraph text."""
    ds = _load("SetFit/20_newsgroups")
    text_key = "text"

    def _to_pairs(split):
        return [(row[text_key], str(row["label_text"])) for row in split]

    train = _to_pairs(ds["train"])
    test_split = ds["test"] if "test" in ds else ds["validation"]
    test = _to_pairs(test_split)
    return BenchmarkDataset(
        name="twenty_newsgroups",
        train=train,
        test=test,
        labels=_collect_string_labels(train + test),
        citation="Lang 1995, 'NewsWeeder: Learning to Filter Netnews'",
    )


# --------------------------------------------------------------------------
# Spam
# --------------------------------------------------------------------------
def load_sms_spam() -> BenchmarkDataset:
    """SMS Spam Collection (Almeida et al. 2011) — binary ham/spam.

    Ships a single ``train`` split, so we carve a deterministic 80/20.
    """
    ds = _load("ucirvine/sms_spam")
    names = _maybe_names(ds["train"].features["label"]) or ["ham", "spam"]
    pairs = _rows_to_pairs(ds["train"], "sms", "label", names)
    train, test = _split_or_carve(pairs)
    return BenchmarkDataset(
        name="sms_spam",
        train=train,
        test=test,
        labels=names,
        citation=(
            "Almeida, Hidalgo & Yamakami 2011, 'Contributions to the Study of SMS Spam Filtering'"
        ),
    )


ALL_TEXT_EXT_LOADERS = [
    load_sst2,
    load_imdb,
    load_rotten_tomatoes,
    load_tweet_offensive,
    load_tweet_hate,
    load_emotion,
    load_tweet_emotion,
    load_dbpedia14,
    load_yahoo_answers,
    load_twenty_newsgroups,
    load_sms_spam,
]

__all__ = [fn.__name__ for fn in ALL_TEXT_EXT_LOADERS] + ["ALL_TEXT_EXT_LOADERS"]
