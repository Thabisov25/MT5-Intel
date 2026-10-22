"""Parameter sweep: the disciplined 'fix, test, fix' loop (D-025/D-026).

For each strategy family, search a small parameter grid. Every candidate is scored
on a TRAIN window only. The single best-by-train candidate is then scored on a
DISJOINT TEST window and must still:
  1. have positive net mean in R,
  2. beat its side-flipped placebo (same timing"),
  3. survive 1.5x cost stress.
Only candidates passing all three on-test are reported as validated. This keeps the
loop honest: we never grade the parameter we tuned on, and every trial is counted
for deflated Sharpe (multiple-testing).
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from .backtest import Bars, simulate, summarize
from .strategies import matched_placebo

# Type of a family function that accepts tunable kwargs and returns signals.
SweepFn = Callable[..., tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]

# Default grids: small and hand-chosen from the design doc priors, not fitted.
GRIDS: dict[str, list[dict[str, Any]]] = {
    "sweep_reclaim": [
        {"k": k, "atr_mult": mult, "confirm_bars": cb}
        for k in (3, 5, 8)
        for mult in (0.05, 0.15)
        # The tolerance dimension: 1 = the original same-bar sweep-and-reclaim, 3 = the
        # sweep arms the setup and the reclaim may arrive up to three bars later.
        for cb in (1, 3)
    ],
    "compression_breakout": [
        {"lookback": lb, "squeeze_q": q}
        for lb in (24, 48, 96)
        for q in (0.15, 0.35)
    ],
    "trend_pullback": [
        {"fast": f, "slow": 200, "k_atr": k}
        for f in (20, 50)
        for k in (1.0, 1.5, 2.0)
    ],
    "range_extreme_mr": [
        {"lookback": lb, "hi_rsi": hi, "lo_rsi": lo}
        for lb in (48, 96)
        for hi, lo in ((65.0, 35.0), (75.0, 25.0))
    ],
    # MOP lookbacks in bars (D1 native: 20~1mo, 60~3mo, 120~6mo, 252~12mo)
    "daily_momentum": [
        {"lookback": lb, "stop_atr": sa}
        for lb in (20, 60, 120, 252)
        for sa in (2.0, 3.0)
    ],
    "carry_trend": [
        {"ma_days": ma, "stop_atr": sa}
        for ma in (100, 200)
        for sa in (3.0, 4.0)
    ],
    # MA crossover: the three pairs the sources name (50/200 long-horizon, 20/50
    # intermediate, 9/21 fast), SMA against EMA, and the exact crossing against the
    # ATR hysteresis band that is the whole point of the family.
    "ma_crossover": [
        {"fast": f, "slow": s, "kind": kind, "buffer_atr": buf}
        for f, s in ((20, 50), (50, 200), (9, 21))
        for kind in ("sma", "ema")
        for buf in (0.0, 0.1)
    ],
    # RSI mean reversion: Wilder's 14/30-70 baseline, the 20/80 selective variant, and the
    # Connors fast RSI(2) reading at 10/90 and 5/95. `entry`/`confirm_bars` compare the
    # literal fade-the-extreme reading with the tolerance (wait for the turn, up to 3 bars).
    "rsi_mean_reversion": [
        {"rsi_period": 14, "lo": 30.0, "hi": 70.0, "entry": "immediate"},
        {"rsi_period": 14, "lo": 30.0, "hi": 70.0, "entry": "confirm", "confirm_bars": 1},
        {"rsi_period": 14, "lo": 30.0, "hi": 70.0, "entry": "confirm", "confirm_bars": 3},
        {"rsi_period": 14, "lo": 20.0, "hi": 80.0, "entry": "confirm", "confirm_bars": 3},
        {"rsi_period": 2, "lo": 10.0, "hi": 90.0, "entry": "immediate"},
        {"rsi_period": 2, "lo": 10.0, "hi": 90.0, "entry": "confirm", "confirm_bars": 3},
        {"rsi_period": 2, "lo": 5.0, "hi": 95.0, "entry": "confirm", "confirm_bars": 3},
    ],
    # Scalping templates (strategies_scalp.py). tp_r sweeps the template's 1.5R
    # assumption instead of trusting it, and stop_mode tests the two honest answers
    # to the cost rule head to head: drop tight-stop signals, or widen the stop.
    "sr_flip": [
        {"swing_k": k, "retest_bars": rb, "tp_r": tp, "stop_mode": m}
        for k in (2, 3)
        for rb in (4, 6)
        for tp in (2.0, 3.0)  # FXOpen cites fixed 1:2 and 1:3 targets
        for m in ("filter", "widen")
    ],
    "pullback_scalp": [
        {"fast": f, "slow": s, "pullback_atr": p, "tp_r": tp, "confirm_bars": cb, "stop_mode": m}
        for f, s in ((20, 50), (8, 21))  # ACY: 20 EMA value line + 50 SMA bias
        for p in (0.5, 1.0)
        for tp in (1.5, 2.0)
        # 1 = confirmation on the very next bar only (the original), 3 = the rejection
        # candle is allowed a small window instead of exactly one bar.
        for cb in (1, 3)
        for m in ("filter", "widen")
    ],
    "orb_london": [
        {"range_minutes": rm, "trigger_atr": t, "stop_ref": sr_, "tp_r": tp, "stop_mode": m}
        for rm in (15.0, 30.0)  # FXOpen's 5/15/30-minute opening ranges
        for t in (0.25,)  # a decisive close beyond the edge, never a bare wick
        for sr_ in ("atr", "opposite")  # the two stop conventions the sources disagree on
        for tp in (2.0, 3.0)  # FXOpen cites 2:1 and 3:1 targets
        for m in ("filter", "widen")
    ],
    "asian_fade": [
        {"fade_atr": f, "target": tg, "confirm_bars": cb, "stop_mode": m}
        for f in (0.25, 0.5)
        for tg in ("mid", "far")  # FXNX: the opposite range edge is the first target
        for cb in (1, 3)  # 1 = the false break and the reclaim on one bar; 3 = a window
        for m in ("filter", "widen")
    ],
}


@dataclass
class SweepCandidate:
    params: dict[str, Any]
    train_mean: float
    train_n: int
    test_mean: float | None
    test_n: int
    placebo_mean: float | None
    stressed_mean: float | None
    validated: bool
    # Cost-free reading of the same trades: separates "no edge" from "eaten by costs".
    gross_train_mean: float | None = None
    gross_test_mean: float | None = None


def gross_bars(bars: Bars) -> Bars:
    """The same bars with the quoted spread zeroed, so a fill carries no cost."""
    return replace(bars, spread_points=np.zeros_like(bars.spread_points))


def _family_call(
    fn: SweepFn,
    bars: Bars,
    params: dict[str, Any],
    cost_mult: float | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Call a family with its swept params, adding the cost budget when supported.

    There is deliberately no fallback to the bare `fn(bars)`: a param the family does not
    accept would otherwise turn the whole grid into N identical default-parameter runs
    while still reporting a sweep. A mismatch is a bug and must be loud.
    """
    kwargs = dict(params)
    if cost_mult is not None and "cost_mult" in inspect.signature(fn).parameters:
        kwargs["cost_mult"] = cost_mult
    try:
        return fn(bars, **kwargs)
    except TypeError as exc:  # pragma: no cover - failure path
        raise TypeError(
            f"{getattr(fn, '__name__', 'family')} rejected sweep params {kwargs}: {exc}"
        ) from exc


def _slice_signals(
    sig_i: np.ndarray,
    side: np.ndarray,
    stops: np.ndarray,
    tp_r: np.ndarray,
    start: int,
    end: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    mask = (sig_i >= start) & (sig_i < end - 1)
    return sig_i[mask], side[mask], stops[mask], tp_r[mask]


def sweep_family(
    bars: Bars,
    family_id: str,
    fn: SweepFn,
    train_start: int,
    train_end: int,
    max_hold_bars: int = 120,
    cost_mult: float | None = None,
) -> list[SweepCandidate]:
    """Grid search on the train window; grade all candidates on train+test."""
    grid = GRIDS.get(family_id)
    if not grid:
        return []
    out: list[SweepCandidate] = []
    for params in grid:
        variant_sig, variant_side, variant_stops, variant_tp = _family_call(
            fn, bars, dict(params), cost_mult
        )
        t_sig, t_side, t_stops, t_tp = _slice_signals(
            variant_sig, variant_side, variant_stops, variant_tp, train_start, train_end
        )
        train = summarize(
            simulate(bars, t_sig, t_side, t_stops, t_tp, max_hold_bars)
        )
        train_mean = train["net_r_mean"]
        if train_mean is None:
            continue
        out.append(
            SweepCandidate(
                params=dict(params),
                train_mean=float(train_mean),
                train_n=int(train["n"]),
                test_mean=None,
                test_n=0,
                placebo_mean=None,
                stressed_mean=None,
                validated=False,
            )
        )
    # Only the train-best earns a look at the test window — one peek, not per-candidate.
    if not out:
        return []
    best_by_train = max(out, key=lambda c: c.train_mean)
    # Re-simulate the winner's window signals for test evaluation.
    w_sig, w_side, w_stops, w_tp = _family_call(
        fn, bars, dict(best_by_train.params), cost_mult
    )
    test_start = train_end
    te_sig, te_side, te_stops, te_tp = _slice_signals(
        w_sig, w_side, w_stops, w_tp, test_start, bars.n
    )
    test = summarize(simulate(bars, te_sig, te_side, te_stops, te_tp, max_hold_bars))
    best_by_train.test_mean = test["net_r_mean"]
    best_by_train.test_n = test["n"]
    if te_sig.size:
        p_sig, p_side = matched_placebo(bars, te_sig, te_side, seed=11)
        best_by_train.placebo_mean = (
            summarize(simulate(bars, p_sig, p_side, te_stops, te_tp, max_hold_bars))["net_r_mean"]
        )
        stressed = summarize(
            simulate(
                bars,
                te_sig,
                te_side,
                te_stops,
                te_tp,
                max_hold_bars,
                spread_pips=bars.spread_pips() * 1.5,
            )
        )
        best_by_train.stressed_mean = stressed["net_r_mean"]
        # Gross: identical signals and stops, zero fill cost. Only the winner is measured,
        # so this adds one simulation per family instead of one per candidate.
        free = gross_bars(bars)
        best_by_train.gross_test_mean = summarize(
            simulate(free, te_sig, te_side, te_stops, te_tp, max_hold_bars)
        )["net_r_mean"]
        tr_sig, tr_side, tr_stops, tr_tp = _slice_signals(
            w_sig, w_side, w_stops, w_tp, train_start, train_end
        )
        best_by_train.gross_train_mean = summarize(
            simulate(free, tr_sig, tr_side, tr_stops, tr_tp, max_hold_bars)
        )["net_r_mean"]
        best_by_train.validated = bool(
            best_by_train.train_mean > 0
            and best_by_train.test_mean is not None
            and best_by_train.test_mean > 0
            and best_by_train.test_n >= 30
            and best_by_train.placebo_mean is not None
            and best_by_train.test_mean > best_by_train.placebo_mean
            and best_by_train.stressed_mean is not None
            and best_by_train.stressed_mean > 0
        )
    return out
