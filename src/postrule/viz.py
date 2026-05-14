# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Plot transition curves from benchmark JSONL output.

matplotlib is a soft dependency — pulled in only when plotting is
requested. All data munging (parsing JSONL, computing crossover depth)
is pure-Python and testable without a display.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BenchmarkRun:
    """Parsed ``postrule bench`` output — summary + checkpoints."""

    benchmark: str
    labels: int
    train_rows: int
    test_rows: int
    seed_size: int
    checkpoints: list[dict[str, Any]] = field(default_factory=list)

    def outcomes(self) -> list[int]:
        return [c["training_outcomes"] for c in self.checkpoints]

    def rule_accs(self) -> list[float]:
        return [c["rule_test_accuracy"] for c in self.checkpoints]

    def ml_accs(self) -> list[float]:
        return [c["ml_test_accuracy"] for c in self.checkpoints]

    def model_accs(self) -> list[float | None]:
        return [c.get("model_test_accuracy") for c in self.checkpoints]

    def has_model(self) -> bool:
        return any(c.get("model_test_accuracy") is not None for c in self.checkpoints)

    def crossover_outcomes(self) -> int | None:
        """Training-outcome count where ML first exceeds the rule."""
        for c in self.checkpoints:
            if c["ml_test_accuracy"] > c["rule_test_accuracy"]:
                return c["training_outcomes"]
        return None

    def transition_depth(self, *, alpha: float = 0.01, prefer_paired: bool = True) -> int | None:
        """Verdict count at which ML beats rule with ``p < alpha``.

        When per-example ``rule_correct``/``ml_correct`` arrays are
        present and ``prefer_paired=True``, uses McNemar's paired
        exact test — the tighter and more principled statistic for
        this comparison. Falls back to an unpaired two-proportion
        z-test when per-example data isn't available. Matches paper
        §4.4 "statistical beat" definition.
        """
        for c in self.checkpoints:
            if not c.get("ml_trained"):
                continue
            p_value: float | None = None
            if (
                prefer_paired
                and c.get("rule_correct") is not None
                and c.get("ml_correct") is not None
            ):
                p_value = mcnemar_p(c["rule_correct"], c["ml_correct"])
            else:
                p_value = _two_proportion_z_p(
                    p1=c["ml_test_accuracy"],
                    p2=c["rule_test_accuracy"],
                    n=self.test_rows,
                )
            if p_value is not None and p_value < alpha:
                return c["training_outcomes"]
        return None

    def final_gap(self) -> float:
        """ML accuracy minus rule accuracy at the last checkpoint."""
        if not self.checkpoints:
            return 0.0
        last = self.checkpoints[-1]
        return last["ml_test_accuracy"] - last["rule_test_accuracy"]


def _two_proportion_z_p(*, p1: float, p2: float, n: int) -> float | None:
    """One-sided two-proportion z-test p-value (H1: p1 > p2).

    Returns None when the pooled variance is zero (degenerate) or when
    ``n`` is non-positive. Pure Python — no scipy dependency.
    """
    if n <= 0:
        return None
    if p1 <= p2:
        return 1.0
    pooled = (p1 + p2) / 2
    var = pooled * (1 - pooled) * (2 / n)
    if var <= 0:
        return None
    import math

    z = (p1 - p2) / math.sqrt(var)
    # One-sided survival: 1 - Phi(z). Use the complementary error function.
    return 0.5 * math.erfc(z / math.sqrt(2))


def mcnemar_p(rule_correct: list[bool], ml_correct: list[bool]) -> float | None:
    """Two-sided McNemar's paired-test p-value.

    Computes ``p = min(1.0, 2 * BinomialCDF(min(b, c); n_paired, 0.5))``
    where ``b`` and ``c`` are the discordant-pair counts and
    ``n_paired = b + c``. This matches the convention used in the
    paper's Algorithm 1 (``body.typ`` §3.2), in
    :func:`postrule.autoresearch._mcnemar_p_value`, and in
    ``tests/test_figure_4_data_contract.py``. Uses the exact binomial
    tail for small ``n``; normal approximation above 50 disagreements.

    ``rule_correct`` and ``ml_correct`` are parallel lists of booleans
    over the same test set. Returns None if the lists are empty or
    mismatched.
    """
    if not rule_correct or not ml_correct or len(rule_correct) != len(ml_correct):
        return None
    b = sum(1 for r, m in zip(rule_correct, ml_correct, strict=True) if (not r) and m)
    c = sum(1 for r, m in zip(rule_correct, ml_correct, strict=True) if r and (not m))
    n = b + c
    if n == 0:
        return 1.0  # no disagreements → cannot reject H0

    if n <= 50:
        # Exact two-sided binomial:
        # p = min(1.0, 2 * P(X <= min(b, c) | X ~ Bin(n, 0.5))).
        from math import comb

        k = min(b, c)
        tail = sum(comb(n, i) for i in range(k + 1))
        p_one = tail / (2**n)
        return min(1.0, 2.0 * p_one)

    # Normal approximation (continuity-corrected), two-sided.
    import math

    # |b - c| with continuity correction toward zero.
    abs_diff = abs(b - c)
    cc = abs_diff - 1 if abs_diff >= 1 else 0
    z = cc / math.sqrt(n)
    # Two-sided p = 2 * (1 - Phi(z)) = erfc(z / sqrt(2)).
    return min(1.0, math.erfc(z / math.sqrt(2)))


def load_run(jsonl_path: str | Path) -> BenchmarkRun:
    path = Path(jsonl_path)
    summary: dict[str, Any] = {}
    checkpoints: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("kind") == "summary":
                summary = row
            elif row.get("kind") == "checkpoint":
                checkpoints.append(row)
    if not summary:
        raise ValueError(f"no summary row found in {path}")
    return BenchmarkRun(
        benchmark=summary["benchmark"],
        labels=summary["labels"],
        train_rows=summary["train_rows"],
        test_rows=summary["test_rows"],
        seed_size=summary["seed_size"],
        checkpoints=checkpoints,
    )


def plot_transition_curves(
    runs: Iterable[BenchmarkRun],
    *,
    output_path: str | Path,
    title: str = "Postrule transition curves",
) -> None:
    """Render a multi-panel transition-curve figure.

    One panel per ``BenchmarkRun``, arranged in a 2×2 grid when ≤4
    runs are supplied (the paper's Figure 1 layout). Each panel plots
    rule accuracy (flat line) vs ML accuracy (rising) against training
    outcomes on a log x-axis.
    """
    try:
        import matplotlib.pyplot as plt  # type: ignore[import-not-found]
    except ImportError as e:
        raise ImportError(
            "plotting requires matplotlib. Install with `pip install postrule[viz]`."
        ) from e

    runs = list(runs)
    if not runs:
        raise ValueError("at least one run is required")

    cols = 2 if len(runs) > 1 else 1
    rows = (len(runs) + cols - 1) // cols
    fig, axes_grid = plt.subplots(
        rows,
        cols,
        figsize=(6.5 * cols, 4.2 * rows),
        squeeze=False,
    )
    axes = [ax for row in axes_grid for ax in row]

    for ax, run in zip(axes, runs, strict=False):
        xs = run.outcomes()
        ax.plot(xs, run.rule_accs(), label="Rule", linestyle="--", color="#c04040")
        ax.plot(xs, run.ml_accs(), label="ML", color="#2a7fb8", linewidth=2)
        if run.has_model():
            model_vals = [v if v is not None else float("nan") for v in run.model_accs()]
            ax.plot(
                xs,
                model_vals,
                label="language model (shadow)",
                color="#5b9e4a",
                linestyle="-.",
                linewidth=1.6,
            )
        crossover = run.crossover_outcomes()
        if crossover is not None:
            ax.axvline(crossover, color="#8a8a8a", linestyle=":", linewidth=1)
            ax.annotate(
                f"crossover ≈ {crossover:,}",
                xy=(crossover, 0.05),
                xytext=(6, 0),
                textcoords="offset points",
                fontsize=8,
                color="#444",
            )
        ax.set_title(f"{run.benchmark}  ({run.labels} labels)")
        ax.set_xlabel("Training outcomes")
        ax.set_ylabel("Test accuracy")
        ax.set_xscale("log")
        ax.set_ylim(0, 1.0)
        ax.grid(True, which="both", alpha=0.25)
        ax.legend(loc="lower right", fontsize=9)

    # Hide any unused subplots (odd run count).
    for extra_ax in axes[len(runs) :]:
        extra_ax.set_visible(False)

    fig.suptitle(title, fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


__all__ = ["BenchmarkRun", "load_run", "plot_transition_curves"]
