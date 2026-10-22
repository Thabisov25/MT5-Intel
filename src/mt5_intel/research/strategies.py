"""Strategy families (causal, closed-bar only) and matched random-level placebos.

Each family is a function bars -> (sig_i, side, stop_pips, tp_r). All features use only
information available at the time the signal bar closes: swing confirmation lags respected,
rolling stats shifted to exclude the forming bar implicitly (signals are evaluated on bar i,
entry next bar).

Family priors from the design doc (§6.5): sweep-reclaim reversal, compression-to-expansion
breakout, pullback-to-value continuation, range-extreme reversion. The doc is explicit that
these are hypotheses to kill or keep — never a preset list of winners.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import NotRequired, TypedDict

import numpy as np

from .backtest import Bars

StrategyFn = Callable[[Bars], tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]


class Family(TypedDict):
    id: str
    description: str
    fn: StrategyFn
    # The settings the registered rule runs with, when a reader cannot tell them from the name.
    # The sweep carries several (three MA pairs share the `ma_crossover` id), so a card that
    # does not say which one it used cannot be reconciled with the lines on a chart.
    params: NotRequired[str]


def _causal_mean(series: np.ndarray, period: int) -> np.ndarray:
    """Trailing mean: out[i] is the mean of series[i-period+1..i], and nothing later.

    `np.convolve(..., mode="same")` is the tempting one-liner here and is wrong: it *centres*
    the window, so out[i] would also average the next `period // 2` bars and a reading on a
    closed bar would depend on bars that had not happened yet. The warm-up borrows the first
    full window rather than the future, as the ATR always has.
    """
    if series.size == 0:
        return series
    if series.size < period:
        return np.full(series.size, float(series.mean()))
    rolled = np.convolve(series, np.ones(period) / period, mode="valid")
    return np.concatenate((np.full(period - 1, rolled[0]), rolled))


def _atr(bars: Bars, period: int = 14) -> np.ndarray:
    hi, lo, cl = bars.high, bars.low, bars.close
    prev_cl = np.concatenate(([cl[0]], cl[:-1]))
    tr = np.maximum(hi - lo, np.maximum(np.abs(hi - prev_cl), np.abs(lo - prev_cl)))
    return _causal_mean(tr, period)


def _sma(series: np.ndarray, period: int) -> np.ndarray:
    """Causal simple moving average: out[i] uses series[i-period+1..i]; earlier bars NaN."""
    n = series.size
    out = np.full(n, np.nan)
    if period < 1 or n < period:
        return out
    cum = np.cumsum(np.insert(series, 0, 0.0))
    out[period - 1:] = (cum[period:] - cum[:-period]) / period
    return out


def _ema(series: np.ndarray, period: int) -> np.ndarray:
    """Causal EMA seeded with the SMA of the first `period` bars; earlier bars NaN.

    Seeding on the average (rather than series[0]) shortens the warm-up and keeps the
    first usable value independent of an arbitrary starting bar.
    """
    n = series.size
    out = np.full(n, np.nan)
    if period < 1 or n < period:
        return out
    alpha = 2 / (period + 1)
    out[period - 1] = float(series[:period].mean())
    for i in range(period, n):
        out[i] = alpha * series[i] + (1 - alpha) * out[i - 1]
    return out


def _wilder_rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder's RSI (New Concepts, 1978): smoothed average gain/loss, close-to-close.

    The seed is the simple mean of the first `period` changes, then Wilder's smoothing
    `avg = (avg * (period - 1) + current) / period`; out[i] uses closes up to and including
    bar i, so the value is knowable when that bar closes. Bars before the seed are NaN.
    A flat run (no gain, no loss) is reported as 50, the neutral reading.
    """
    n = close.size
    out = np.full(n, np.nan)
    if period < 1 or n <= period:
        return out
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = float(gain[:period].mean())
    avg_loss = float(loss[:period].mean())

    def _value(g: float, l: float) -> float:
        if l <= 0.0:
            return 100.0 if g > 0.0 else 50.0
        return 100.0 - 100.0 / (1.0 + g / l)

    out[period] = _value(avg_gain, avg_loss)
    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gain[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + loss[i - 1]) / period
        out[i] = _value(avg_gain, avg_loss)
    return out


# How many bars a swing level stays worth trading against before it is a different market.
STALE_LEVEL_BARS = 400


def _swing_lows(bars: Bars, k: int) -> np.ndarray:
    """Boolean array: bar i is a confirmed swing low after k later bars have closed."""
    n = bars.n
    out = np.zeros(n, dtype=bool)
    lo = bars.low
    for i in range(n - k):
        if lo[i] == min(lo[max(0, i - k): i + k + 1]):
            out[i + k] = True  # confirmed only k bars later
    return out


def _swing_highs(bars: Bars, k: int) -> np.ndarray:
    n = bars.n
    out = np.zeros(n, dtype=bool)
    hi = bars.high
    for i in range(n - k):
        if hi[i] == max(hi[max(0, i - k): i + k + 1]):
            out[i + k] = True
    return out


def sweep_and_reclaim(
    bars: Bars,
    k: int = 5,
    atr_mult: float = 0.05,
    confirm_bars: int = 1,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Sweep of a confirmed swing level then reclaim close back inside -> reversal.

    Long: price wicks below a prior confirmed swing low by >= atr_mult ATR, closes back
    above it. Short: mirror at swing highs. Kill criterion is the placebo comparison, not
    the rules. atr_mult is swept (0.05/0.15): it is the definition of "swept enough".

    `confirm_bars` is the tolerance on when the reclaim may arrive. At the default of 1 the
    sweep and the reclaim must share one bar and the level is a line - the original, rigid
    reading. Above 1 the sweep *arms* the setup, the swept level is frozen, and the first
    bar within the window that closes back across that level fires; the setup expires after
    `confirm_bars` bars. This is the difference between demanding an exact one-candle
    formation and recognising the same event when it takes two or three candles to complete.
    """
    n = bars.n
    atr = _atr(bars)
    sl_low, sl_high = _swing_lows(bars, k), _swing_highs(bars, k)
    # levels known at bar i: most recent confirmed swing values (confirmed k bars ago)
    last_low_val = np.full(n, np.nan)
    last_high_val = np.full(n, np.nan)
    last_low_i = np.full(n, -1)
    last_high_i = np.full(n, -1)
    cur_l, cur_li = np.nan, -1
    cur_h, cur_hi = np.nan, -1
    for i in range(n):
        if sl_low[i]:
            # the swing bar is i-k; its low becomes a level once confirmed
            cur_l, cur_li = bars.low[i - k], i - k
        if sl_high[i]:
            cur_h, cur_hi = bars.high[i - k], i - k
        last_low_val[i], last_low_i[i] = cur_l, cur_li
        last_high_val[i], last_high_i[i] = cur_h, cur_hi
    sig_i, side = [], []
    pip = bars.pip()
    pending = 0          # +1: a downside sweep is armed, -1: an upside sweep is armed
    arm_i = -1
    arm_level = np.nan
    for i in range(1, n - 1):
        # A swing sweep waits for its reclaim on its own bars; nothing else may arm while
        # it is pending, so a setup cannot be quietly replaced by a fresher one.
        if pending != 0:
            c_now = float(bars.close[i])
            reclaimed = c_now > arm_level if pending > 0 else c_now < arm_level
            if reclaimed:
                sig_i.append(i)
                side.append(pending)
                pending = 0
                continue
            if i - arm_i >= confirm_bars:
                pending = 0  # window spent: this bar may arm a fresh setup below
            else:
                continue
        a = atr[i]
        if np.isnan(a) or a <= 0 or np.isnan(last_low_val[i]) or np.isnan(last_high_val[i]):
            continue
        # A level older than 400 bars anchors nothing: it is a different market. Either side
        # being stale is enough to skip the bar - testing `and` here meant a sweep could still
        # arm off a level thousands of bars old whenever the other side happened to be fresh,
        # which is the "ancient anchor" this guard exists to prevent.
        if i - last_low_i[i] > STALE_LEVEL_BARS or i - last_high_i[i] > STALE_LEVEL_BARS:
            continue
        level_l, level_h = last_low_val[i], last_high_val[i]
        o, h, lo, c = bars.open_[i], bars.high[i], bars.low[i], bars.close[i]
        long_sweep = lo < level_l - atr_mult * a
        short_sweep = h > level_h + atr_mult * a
        # long: sweep below low level, reclaim above it
        if long_sweep and c > level_l and o >= level_l:
            sig_i.append(i)
            side.append(1)
        # short: sweep above high level, reclaim below it
        elif short_sweep and c < level_h and o <= level_h:
            sig_i.append(i)
            side.append(-1)
        elif confirm_bars > 1:
            # No same-bar reclaim: arm the sweep and let a later bar complete it.
            if long_sweep:
                pending, arm_i, arm_level = 1, i, level_l
            elif short_sweep:
                pending, arm_i, arm_level = -1, i, level_h
    sig = np.array(sig_i, dtype=np.int64)
    sd = np.array(side, dtype=np.int64)
    stops = np.maximum(2.0 * np.where(np.isnan(atr[sig]), 0, atr[sig]) / pip, 5.0) if sig.size else np.zeros(0)
    return sig, sd, stops, np.full(sig.size, 2.0)


def compression_breakout(bars: Bars, lookback: int = 48, squeeze_q: float = 0.25) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Range compression then breakout close outside the compressed box (H-002)."""
    n = bars.n
    pip = bars.pip()
    hi_box = np.array([bars.high[max(0, i - lookback):i].max() if i >= lookback else np.nan for i in range(n)])
    lo_box = np.array([bars.low[max(0, i - lookback):i].min() if i >= lookback else np.nan for i in range(n)])
    width = hi_box - lo_box
    rolling = np.array([width[max(0, i - 200):i][~np.isnan(width[max(0, i - 200):i])].min()
                        if i >= 200 and np.any(~np.isnan(width[max(0, i - 200):i])) else np.nan for i in range(n)])
    sig_i, side = [], []
    for i in range(201, n - 1):
        if np.isnan(hi_box[i]) or np.isnan(rolling[i]) or rolling[i] <= 0:
            continue
        if width[i] > rolling[i] * (1 + squeeze_q):
            continue  # not compressed vs recent minimum box
        c = bars.close[i]
        if c > hi_box[i]:
            sig_i.append(i); side.append(1)
        elif c < lo_box[i]:
            sig_i.append(i); side.append(-1)
    sig = np.array(sig_i, dtype=np.int64)
    sd = np.array(side, dtype=np.int64)
    atr = _atr(bars)
    stops = np.maximum(atr[sig] / pip, 5.0) if sig.size else np.zeros(0)
    return sig, sd, stops, np.full(sig.size, 2.0)


def trend_pullback(bars: Bars, fast: int = 50, slow: int = 200, k_atr: float = 1.5) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """EMA trend filter; enter on pullback of k_atr*ATR from fast EMA, in trend direction."""
    n = bars.n
    pip = bars.pip()

    def ema(series: np.ndarray, period: int) -> np.ndarray:
        a = 2 / (period + 1)
        out = np.empty_like(series)
        out[0] = series[0]
        for i in range(1, series.size):
            out[i] = a * series[i] + (1 - a) * out[i - 1]
        return out

    ef, es = ema(bars.close, fast), ema(bars.close, slow)
    atr = _atr(bars)
    sig_i, side = [], []
    for i in range(slow + 1, n - 1):
        if ef[i] == es[i]:
            continue
        trend_up = ef[i] > es[i]
        # long: price dipped at least k_atr*ATR below fast ema while trend up
        if trend_up and (ef[i] - bars.low[i]) >= k_atr * atr[i]:
            sig_i.append(i); side.append(1)
        elif (not trend_up) and (bars.high[i] - ef[i]) >= k_atr * atr[i]:
            sig_i.append(i); side.append(-1)
    sig = np.array(sig_i, dtype=np.int64)
    sd = np.array(side, dtype=np.int64)
    stops = np.maximum(k_atr * atr[sig] / pip, 5.0) if sig.size else np.zeros(0)
    return sig, sd, stops, np.full(sig.size, 2.0)


def range_extreme_reversion(bars: Bars, lookback: int = 96, rsi_period: int = 14, hi_rsi: float = 65.0, lo_rsi: float = 35.0) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """At the edge of a multi-hour range with RSI stretched -> fade back to range middle."""
    n = bars.n
    pip = bars.pip()
    # The project's one RSI: Wilder's. This family used to build its own out of a simple moving
    # average of gains and losses, which is a different indicator - 6 points apart at the median
    # on H1 and 14 apart at the 90th percentile, so on ~14% of bars the two disagreed about the
    # very 65/35 line this rule trades. A card quoted against one RSI and taken on another is
    # unreviewable, so there is now one reading and the panel can print it.
    rsi = _wilder_rsi(bars.close, rsi_period)
    sig_i, side = [], []
    for i in range(lookback + 1, n - 1):
        box_hi = bars.high[i - lookback:i].max()
        box_lo = bars.low[i - lookback:i].min()
        range_ = box_hi - box_lo
        if range_ <= 0:
            continue
        pos = (bars.close[i] - box_lo) / range_
        if pos > 0.95 and rsi[i] > hi_rsi:
            sig_i.append(i); side.append(-1)
        elif pos < 0.05 and rsi[i] < lo_rsi:
            sig_i.append(i); side.append(1)
    sig = np.array(sig_i, dtype=np.int64)
    sd = np.array(side, dtype=np.int64)
    atr = _atr(bars)
    stops = np.maximum(1.5 * atr[sig] / pip, 5.0) if sig.size else np.zeros(0)
    return sig, sd, stops, np.full(sig.size, 2.0)


def daily_momentum(
    bars: Bars,
    lookback: int = 120,
    stop_atr: float = 3.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """MOP-style time-series momentum, daily-bar adaptation (spot-price proxy).

    Signal: sign of the trailing `lookback`-bar log return, evaluated on a closed bar;
    entry at the next bar's open. Re-enters on flips. ATR(20) stop as the harness's
    risk unit; the cash-vs-futures caveat is in docs/RESEARCH_PLAN_MOMENTUM_CARRY.md.
    """
    n = bars.n
    pip = bars.pip()
    log_close = np.log(bars.close)
    sig_i, side = [], []
    for i in range(lookback, n - 1):
        past = log_close[i] - log_close[i - lookback]
        prev = log_close[i - 1] - log_close[i - 1 - lookback]
        now_sign = 1 if past > 0 else -1
        prev_sign = 1 if prev > 0 else -1
        if now_sign != prev_sign:  # flip after a closed bar -> trade next open
            sig_i.append(i)
            side.append(now_sign)
    sig = np.array(sig_i, dtype=np.int64)
    sd = np.array(side, dtype=np.int64)
    atr = _atr(bars, 20)
    stops = np.maximum(stop_atr * atr[sig] / pip, 5.0) if sig.size else np.zeros(0)
    return sig, sd, stops, np.full(sig.size, 2.0)


def carry_trend(
    bars: Bars,
    ma_days: int = 200,
    stop_atr: float = 4.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Carry-gated trend (candidate B in the research): position only when the

    policy-rate spread and the slow trend agree; flat when they disagree.
    Carry sign from the policy-rate table (rates.py); trend = close vs MA(ma_days).
    A position starts when agreement begins; ends (reversed entry) when it breaks.
    """
    from .rates import carry_direction

    n = bars.n
    pip = bars.pip()
    ma = np.convolve(bars.close, np.ones(ma_days) / ma_days, mode="valid")
    # pad so ma[i] uses bars up to and including i
    ma_full = np.concatenate((np.full(ma_days - 1, np.nan), ma))
    # The rate table is keyed on the calendar month, so the lookup wants the UTC date. Bar stamps
    # are the broker's clock: on a UTC+3 terminal every evening after 21:00 UTC is stamped as the
    # *next* day, so a policy change on the first of a month would be applied a few hours early.
    utc = bars.utc_time()
    sig_i, side = [], []
    for i in range(ma_days, n - 1):
        carry = carry_direction(int(utc[i]))
        trend = 1 if bars.close[i] > ma_full[i] else -1
        if carry == 0 or np.isnan(ma_full[i]):
            continue
        prev_carry = carry_direction(int(utc[i - 1]))
        prev_trend = 1 if bars.close[i - 1] > ma_full[i - 1] else -1
        agree_now = carry == trend
        agree_prev = prev_carry == prev_trend
        first_eligible = i == ma_days
        if agree_now and (not agree_prev or first_eligible):  # agreement begins -> take that side
            sig_i.append(i)
            side.append(carry)
    sig = np.array(sig_i, dtype=np.int64)
    sd = np.array(side, dtype=np.int64)
    atr = _atr(bars, 20)
    stops = np.maximum(stop_atr * atr[sig] / pip, 5.0) if sig.size else np.zeros(0)
    return sig, sd, stops, np.full(sig.size, 2.0)


def ma_crossover(
    bars: Bars,
    fast: int = 20,
    slow: int = 50,
    kind: str = "sma",
    buffer_atr: float = 0.0,
    stop_atr: float = 1.5,
    tp_r: float = 2.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Fast/slow moving-average crossover - the textbook trend-following entry.

    Long when the fast average crosses above the slow one, short when it crosses below.
    Sources: 50/200 SMA is the familiar long-horizon pair (Investopedia's golden cross),
    20/50 is the intermediate pair StockCharts/OANDA describe, and 9/21 EMA is a faster
    variant practitioners use - so the sweep carries all three and the default is the
    plainest one (20/50 SMA). Signals are read on closed bars and entered at the next
    open (D-012); both averages must have their full window of history first.

    A crossover is the most brittle shape in this file: it fires on the exact bar the two
    lines trade places, and any microscopic crossing in a flat market is a whipsaw. So the
    rule carries an explicit tolerance instead of a razor edge. `buffer_atr` is a hysteresis
    band in ATR(14) units: the separation between the averages must exceed it before the
    state may flip, and the state is *held* inside the band. At the default 0.0 this is the
    exact textbook crossover - the fragile reference the tolerance exists to test against.
    Above 0 it is the same rule read as a rough pattern ("the fast line is meaningfully above
    the slow line") rather than an exact point, which is what removes the whipsaw.
    """
    if kind not in ("sma", "ema"):
        raise ValueError(f"kind must be 'sma' or 'ema', got {kind!r}")
    if fast >= slow:
        raise ValueError(f"fast must be shorter than slow (fast={fast}, slow={slow})")
    ma = _sma if kind == "sma" else _ema
    ef, es = ma(bars.close, fast), ma(bars.close, slow)
    atr = _atr(bars, 14)
    pip = bars.pip()
    n = bars.n
    sig_i, side = [], []
    state = 0  # 0 flat/undecided, +1 long, -1 short
    warmed = False
    for i in range(slow, n - 1):
        a = float(atr[i])
        if not np.isfinite(ef[i]) or not np.isfinite(es[i]) or not np.isfinite(a) or a <= 0:
            continue
        separation = float(ef[i] - es[i])
        threshold = buffer_atr * a
        if separation > threshold:
            new_state = 1
        elif separation < -threshold:
            new_state = -1
        else:
            new_state = state  # inside the tolerance band: hold, never whipsaw
        if not warmed:
            # Adopt the prevailing direction on the first usable bar without trading it:
            # a signal at an arbitrary sample start would be a backtest artifact, not a cross.
            state, warmed = new_state, True
            continue
        if new_state != state:
            state = new_state
            if new_state != 0:
                sig_i.append(i)
                side.append(new_state)
    sig = np.array(sig_i, dtype=np.int64)
    sd = np.array(side, dtype=np.int64)
    stops = np.maximum(stop_atr * atr[sig] / pip, 5.0) if sig.size else np.zeros(0)
    return sig, sd, stops, np.full(sig.size, tp_r)


def rsi_mean_reversion(
    bars: Bars,
    rsi_period: int = 14,
    lo: float = 30.0,
    hi: float = 70.0,
    entry: str = "confirm",
    confirm_bars: int = 3,
    stop_atr: float = 1.5,
    tp_r: float = 2.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """RSI mean reversion: buy an oversold reading, sell an overbought one.

    Wilder's RSI(14) with the 30/70 levels is the canonical baseline (the default), and 20/80
    is the more selective variant the sweep carries. `entry` picks how the reading is used:

      * "immediate" - fade the bar whose RSI closes beyond the level. This is the literal
        "buy below 30 / sell above 70" reading. It is the fastest version and the one most
        likely to be early: the sources are explicit that an extreme reading is an alert,
        not a turn, and RSI can sit beyond 70 in a trend for a long time.
      * "confirm" (default) - arm the setup on that bar and wait for RSI to close back
        through the level, within `confirm_bars` bars, before entering. This is the tolerance:
        it does not require the turn to happen on one specific bar, only that the exhaustion
        is acknowledged before the window expires.

    A Connors-style fast variant (RSI(2) at 10/90, and 5/95) rides along in the sweep as the
    short-horizon comparison. Stops and targets stay in the harness's own convention (an ATR
    stop and a fixed R multiple); the RSI-return-to-50 and time-stop exits the sources mention
    are not modelled by the triple barrier and are left for a later pass.
    """
    if entry not in ("confirm", "immediate"):
        raise ValueError(f"entry must be 'confirm' or 'immediate', got {entry!r}")
    if not 0.0 < lo < hi < 100.0:
        raise ValueError(f"levels must satisfy 0 < lo < hi < 100 (lo={lo}, hi={hi})")
    n = bars.n
    rsi = _wilder_rsi(bars.close, rsi_period)
    atr = _atr(bars, 14)
    pip = bars.pip()
    sig_i, side = [], []
    armed = 0      # +1 long armed (oversold), -1 short armed (overbought)
    arm_i = -1
    for i in range(rsi_period + 1, n - 1):
        r = float(rsi[i])
        a = float(atr[i])
        if not np.isfinite(r) or not np.isfinite(a) or a <= 0:
            continue
        if armed != 0:
            back = r > lo if armed > 0 else r < hi
            if back:
                sig_i.append(i)
                side.append(armed)
                armed = 0
                continue
            if i - arm_i >= confirm_bars:
                armed = 0  # the reading never turned: setup expired, this bar may re-arm
            else:
                continue
        if r < lo:
            if entry == "immediate":
                sig_i.append(i)
                side.append(1)
            else:
                armed, arm_i = 1, i
        elif r > hi:
            if entry == "immediate":
                sig_i.append(i)
                side.append(-1)
            else:
                armed, arm_i = -1, i
    sig = np.array(sig_i, dtype=np.int64)
    sd = np.array(side, dtype=np.int64)
    stops = np.maximum(stop_atr * atr[sig] / pip, 5.0) if sig.size else np.zeros(0)
    return sig, sd, stops, np.full(sig.size, tp_r)


FAMILIES: list[Family] = [
    Family(
        id="sweep_reclaim",
        description="Sweep of prior confirmed swing level then reclaim close back inside (H-001)",
        fn=sweep_and_reclaim,
    ),
    Family(
        id="compression_breakout",
        description="Range compression then close-breakout of the box (H-002)",
        fn=compression_breakout,
    ),
    Family(
        id="trend_pullback",
        description="EMA(50)>EMA(200) trend, pullback >= 1.5 ATR to fast EMA (H-004 trend family)",
        fn=trend_pullback,
    ),
    Family(
        id="range_extreme_mr",
        description="Fade >=95th percentile of 96-bar range with stretched RSI (H-005 range family)",
        fn=range_extreme_reversion,
    ),
    Family(
        id="daily_momentum",
        description="MOP-adapted TSMOM: sign of 120-bar log return, re-enter on flips (H-006)",
        fn=daily_momentum,
    ),
    Family(
        id="carry_trend",
        description="USD-JPY policy spread agrees with close vs MA200 trend, else flat (H-007)",
        fn=carry_trend,
    ),
    Family(
        id="ma_crossover",
        description="Fast/slow MA crossover (default 20/50 SMA) with an optional ATR hysteresis "
        "band instead of an exact crossing (H-008)",
        fn=ma_crossover,
        # Named because the sweep runs 9/21 EMA, 20/50 SMA and 50/200 SMA under this one id:
        # this is the pair the live card is about, and the one to look at on a chart.
        params="20/50 SMA",
    ),
    Family(
        id="rsi_mean_reversion",
        description="Wilder RSI(14) 30/70 mean reversion; the oversold/overbought reading arms "
        "the setup and the turn back through the level confirms it (H-009)",
        fn=rsi_mean_reversion,
    ),
]


def matched_placebo(bars: Bars, real_sig_i: np.ndarray, real_side: np.ndarray, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Side-flipped control: the same bars, the same stops, every trade taken the other way.

    What this is, precisely: a deterministic mirror, not a random-level draw. It keeps the
    timing, the frequency and the stop distribution and removes only the direction, so beating
    it answers "is this rule's side information worth anything?" - it is the test the docs call
    the side-flipped placebo, and it is what every recorded trial was graded against. It is
    *not* a control for luck: a mirror cannot lose to the same luck the real rule had. The
    stronger random-level control D-008 asks for (random entries matched by hour and regime)
    is still owed, and `seed` does nothing here - it is kept so callers need not change when
    that control lands.
    """
    flipped = -real_side if real_sig_i.size else real_side
    return real_sig_i.copy(), flipped if real_sig_i.size else np.zeros(0, dtype=np.int64)
