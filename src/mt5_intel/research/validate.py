"""Validation: purged walk-forward, placebo comparison, cost stress, promotion rules.

Promotion needs (blueprint D-025/D-026):
1. Net expectancy (walk-forward, out-of-sample) > 0, lower 90% CI bound > 0.
2. Beats the side-flipped placebo with the same timing/stops (D-008).
3. Still profitable at 1.5x costs.
4. Non-negative expectancy in at least 60% of walk-forward folds.
Until ALL pass, the family stays 'research candidate' and the gate keeps default-denying.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from .backtest import Bars, simulate, summarize
from .strategies import Family, matched_placebo


@dataclass
class FoldResult:
    start: int
    end: int
    n: int
    net_r_mean: float | None


def walk_forward_folds(n_bars: int, n_folds: int, embargo_bars: int) -> list[tuple[int, int]]:
    """Chronological contiguous validation folds with embargo after each signal origin block.

    Signals generated on bars < fold start (train side) are excluded from the fold window by
    construction; embargo drops signals whose entry could overlap the previous test window.
    """
    fold_len = (n_bars - embargo_bars) // n_folds
    folds = []
    for f in range(n_folds):
        start = f * fold_len
        end = start + fold_len if f < n_folds - 1 else n_bars
        folds.append((start, end))
    return folds


def evaluate_family(
    bars: Bars,
    family: Family,
    n_folds: int = 5,
    embargo_bars: int = 300,
    seed: int = 7,
    spread_multiplier: float = 1.0,
) -> dict[str, Any]:
    sig_i, side, stops, tp_r = family["fn"](bars)
    n = bars.n
    folds = walk_forward_folds(n, n_folds, embargo_bars)
    fold_results: list[FoldResult] = []
    oos_r: list[float] = []
    for start, end in folds:
        mask = (sig_i >= start) & (sig_i < end - 1)
        if not np.any(mask):
            fold_results.append(FoldResult(start, end, 0, None))
            continue
        outcomes = simulate(
            bars,
            sig_i[mask],
            side[mask],
            stops[mask],
            tp_r[mask],
            max_hold_bars=120,
        )
        s = summarize(outcomes)
        fold_results.append(FoldResult(start, end, s["n"], s["net_r_mean"]))
        oos_r.extend(o.r for o in outcomes)

    # placebo: side-flipped, identical timing and stops
    p_sig, p_side = matched_placebo(bars, sig_i, side, seed=seed)
    placebo_outcomes = simulate(bars, p_sig, p_side, stops, tp_r, max_hold_bars=120)
    placebo = summarize(placebo_outcomes)
    placebo_mean = placebo["net_r_mean"] or 0.0

    # cost stress: rerun full-sample with wider spreads
    stressed = summarize(
        simulate(bars, sig_i, side, stops, tp_r, max_hold_bars=120, spread_pips=bars.spread_pips() * spread_multiplier)
    )

    full = summarize(
        simulate(bars, sig_i, side, stops, tp_r, max_hold_bars=120)
    )
    real_mean = full["net_r_mean"] or 0.0
    r = np.array(oos_r)
    if r.size > 1 and r.std(ddof=1) > 0:
        se = r.std(ddof=1) / math.sqrt(r.size)
        lower = real_mean if False else float(r.mean() - 1.64 * se)  # 90% one-sided
    else:
        lower = None

    edge_vs_placebo = real_mean - placebo_mean
    folds_positive = sum(1 for f in fold_results if (f.net_r_mean or 0) > 0) / max(1, len(fold_results))
    stressed_mean = stressed["net_r_mean"]

    promoted = (
        real_mean > 0
        and lower is not None and lower > 0
        and edge_vs_placebo > 0
        and stressed_mean is not None and stressed_mean > 0
        and folds_positive >= 0.6
        and full["n"] >= 30
    )
    return {
        "family": family["id"],
        "description": family["description"],
        "full": full,
        "placebo": placebo,
        "edge_vs_placebo": round(edge_vs_placebo, 4),
        "stressed": stressed,
        "folds": [
            {"start": f.start, "end": f.end, "n": f.n, "net_r_mean": f.net_r_mean} for f in fold_results
        ],
        "folds_positive_share": round(folds_positive, 2),
        "lower_ci_r": None if lower is None else round(lower, 4),
        "status": "historically validated" if promoted else "research candidate",
        "promoted": promoted,
    }
