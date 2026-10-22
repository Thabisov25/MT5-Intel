"""Scalping strategy families (5 min to intraday holds), codeable templates.

Provenance (docs/RESEARCH_PLAN_MOMENTUM_CARRY.md and the 2026 scalping research
note): sources support the *concepts* (zones not lines, break-retest flips,
pullback-to-value, session ranges) but publish no verified deterministic recipe.
The numbered rules below are transparent test templates adapted from the
researcher's templates — not claimed winners. All rules use only closed bars;
entries are the next bar's open (D-012 causal convention).

Session definitions use UTC session starts for research simplicity; the live
system keeps Tokyo/London/NY local-clock definitions (D-011). That reconciliation
is now enforced rather than promised: bar stamps are the BROKER's clock, so a rule
that names a UTC hour reads `Bars.utc_offset_s` first and raises SessionClockError
when nobody has read that clock, instead of trading three hours early. Window
durations are given in minutes and converted with the series' own bar interval, so
"the first 30 minutes of the London hour" means 30 minutes on every timeframe -
and is refused, not widened to six hours, on a timeframe that cannot resolve it.

Cost rule (research-derived): a stop closer than MIN_STOP_SPREAD_MULT x the bar's
quoted spread means all-in round-trip cost exceeds ~20% of the risk. That rule is
applied in one of three modes (the sweep tests both real modes head to head):
  - "filter" (default): drop the signal, because the structural stop is too close;
  - "widen": keep the signal but push the stop out to the cost floor (risk-based
    sizing absorbs the wider stop; the target stays tp_r x the new stop);
  - "off": return raw signals with the rules' own stops (used only to measure how
    many signals the cost rule is actually removing).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

from .backtest import Bars
from .strategies import Family, _atr, _swing_highs, _swing_lows

# A family function: bars (+ tunable kwargs) -> (sig_i, side, stop_pips, tp_r)
ScalpFn = Callable[..., tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]

MIN_STOP_SPREAD_MULT = 10.0  # stop >= 10 x spread keeps round-trip cost < 20% of risk
MIN_STOP_PIPS = 1.0  # never risk less than a pip, whatever the quoted spread says
STOP_MODES = ("filter", "widen", "off")


class SessionClockError(RuntimeError):
    """A UTC session rule was run without knowing how far the broker's clock is from UTC."""


# A rule whose window is a wall-clock duration, converted to bars. Kept as explicit arguments
# because the duration is the rule: "the first 30 minutes of the London hour" is a different
# rule from "the first six bars", and only one of them survives a change of timeframe.
ORB_RANGE_MINUTES = 30.0      # FXOpen: 5/15/30-minute opening ranges
ORB_SCAN_MINUTES = 120.0      # stay inside the session the range belongs to
ASIAN_FADE_MINUTES = 120.0    # "~2h after the range sets"


def _utc_hours(bars: Bars) -> np.ndarray:
    """UTC hour (0-23) of each bar open.

    Bar stamps are the BROKER's clock (`Bars.utc_offset_s`), not UTC. Reading the hour straight
    off the stamp - which this did - put London's 07:00 opening range at 04:00 UTC on a UTC+3
    terminal and the Asian range at 21:00 UTC, three hours from the session each rule names. A
    session rule that cannot get the offset refuses rather than trading a clock nobody has read
    (D-011: "must reconcile before any card is actionable").
    """
    if bars.utc_offset_s is None:
        raise SessionClockError(
            "this rule trades a UTC session and the broker's clock offset is unknown; "
            "load the bars with utc_offset_s (see backtest.resolve_offset_s)"
        )
    return (bars.utc_time() // 3600) % 24


def _utc_days(bars: Bars) -> np.ndarray:
    """UTC calendar day of each bar open, so a session window cannot straddle one."""
    return bars.utc_time() // 86_400


def bar_minutes(bars: Bars) -> float:
    """The bar interval in minutes, measured from the series itself.

    A window given in minutes has to become a count of bars, and that count depends on the
    timeframe: 30 minutes is six bars on M5 and half of one on H1. Measuring it here means no
    caller can hand a rule the wrong conversion, and the weekend gap is dropped because a
    three-day hole is not the bar interval.
    """
    if bars.n < 2:
        return 60.0
    diffs = np.diff(bars.time)
    diffs = diffs[(diffs > 0) & (diffs <= 4 * 3600)]
    return float(np.median(diffs)) / 60.0 if diffs.size else 60.0


def bars_for(minutes: float, per_bar_minutes: float, at_least: int = 1) -> int:
    """How many bars cover `minutes` at this timeframe, never fewer than `at_least`."""
    if per_bar_minutes <= 0:
        return at_least
    return max(at_least, round(minutes / per_bar_minutes))


def _cost_floor(spread_pips: np.ndarray, sig: np.ndarray,
                mult: float = MIN_STOP_SPREAD_MULT) -> np.ndarray:
    """Stop width (pips) below which round-trip cost exceeds ~20% of the risk."""
    if sig.size == 0:
        return np.zeros(0)
    floor: np.ndarray = np.maximum(mult * spread_pips[sig], MIN_STOP_PIPS)
    return floor


def _cost_adjust(
    sig: np.ndarray,
    side: np.ndarray,
    stops: np.ndarray,
    tp_r: np.ndarray,
    spread_pips: np.ndarray,
    mode: str,
    mult: float = MIN_STOP_SPREAD_MULT,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Apply the cost rule in 'filter', 'widen' or 'off' mode (see module docstring)."""
    if mode not in STOP_MODES:
        raise ValueError(f"stop_mode must be one of {STOP_MODES}, got {mode!r}")
    if mode == "off" or sig.size == 0:
        return sig, side, stops, tp_r
    floor = _cost_floor(spread_pips, sig, mult)
    if mode == "widen":
        return sig, side, np.maximum(stops, floor), tp_r
    keep = stops >= floor
    return sig[keep], side[keep], stops[keep], tp_r[keep]


def sr_flip(
    bars: Bars,
    swing_k: int = 2,
    retest_bars: int = 6,
    zone_mult: float = 0.5,
    tp_r: float = 2.0,
    stop_mode: str = "filter",
    cost_mult: float = MIN_STOP_SPREAD_MULT,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Support/resistance flip scalp: break of a fractal zone, retest, continue.

    1. Level: swing high/low confirmed k bars later (fractal k-left/k-right).
    2. Zone: level +/- zone_mult * ATR(14).
    3. Break: close beyond the zone edge.
    4. Retest: within retest_bars, a bar trades back into the zone and closes
       beyond the breakout edge again -> signal on that close, enter next open.
    5. Stop: beyond the retest bar's extreme by 0.5 * ATR (MultiBank's 0.5-1.0 ATR
       beyond the zone; FXOpen says "a few pips" past the retest extreme).
       Target: tp_r * stop, tested at the 1:2 and 1:3 the sources cite.
    """
    n = bars.n
    atr = _atr(bars, 14)
    hi_conf = _swing_highs(bars, swing_k)
    lo_conf = _swing_lows(bars, swing_k)
    spread = bars.spread_pips()

    sig_i: list[int] = []
    side_l: list[int] = []
    stops_l: list[float] = []
    # Track most recent confirmed swing levels and pending break/retest state.
    lvl_hi = lvl_lo = np.nan
    pending_dir = 0          # +1 broke up awaiting retest, -1 broke down
    pending_bars_left = 0
    breakout_edge = np.nan

    for i in range(n):
        if hi_conf[i]:
            lvl_hi = float(bars.high[i - swing_k])
        if lo_conf[i]:
            lvl_lo = float(bars.low[i - swing_k])
        a = float(atr[i])
        if a <= 0 or np.isnan(lvl_hi) or np.isnan(lvl_lo):
            continue
        zone = zone_mult * a
        c, h, lo = float(bars.close[i]), float(bars.high[i]), float(bars.low[i])

        if pending_dir != 0:
            pending_bars_left -= 1
            if pending_dir > 0 and lo <= lvl_hi + zone and c > breakout_edge:
                entry = float(bars.open_[i + 1]) if i + 1 < n else c
                stop_pips = (entry - lo + 0.5 * a) / bars.pip()
                sig_i.append(i); side_l.append(1); stops_l.append(max(stop_pips, 0.0))
                pending_dir = 0
                continue
            if pending_dir < 0 and h >= lvl_lo - zone and c < breakout_edge:
                entry = float(bars.open_[i + 1]) if i + 1 < n else c
                stop_pips = (h - entry + 0.5 * a) / bars.pip()
                sig_i.append(i); side_l.append(-1); stops_l.append(max(stop_pips, 0.0))
                pending_dir = 0
                continue
            if pending_bars_left <= 0:
                pending_dir = 0
            continue  # a pending setup owns the bar; no new breaks while pending

        if c > lvl_hi + zone:  # close above the resistance zone -> pending retest
            pending_dir = 1
            pending_bars_left = retest_bars
            breakout_edge = lvl_hi + zone
        elif c < lvl_lo - zone:
            pending_dir = -1
            pending_bars_left = retest_bars
            breakout_edge = lvl_lo - zone

    sig = np.array(sig_i, dtype=np.int64)
    sd = np.array(side_l, dtype=np.int64)
    stops = np.array(stops_l, dtype="float64") if stops_l else np.zeros(0)
    sig, sd, stops, tps = _cost_adjust(
        sig, sd, stops, np.full(sig.size, tp_r), spread, stop_mode, cost_mult
    )
    return sig, sd, stops, tps


def pullback_scalp(
    bars: Bars,
    fast: int = 20,
    slow: int = 50,
    pullback_atr: float = 0.75,
    tp_r: float = 1.5,
    confirm_bars: int = 1,
    stop_mode: str = "filter",
    cost_mult: float = MIN_STOP_SPREAD_MULT,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Retracement scalp: intraday trend, pullback into the value line (ACY LTA lesson).

    1. Trend: the slow SMA is the bias line, the fast EMA the momentum/value line
       (ACY uses a 50 SMA plus a 20 EMA; long needs price above both).
    2. Pullback: a bar trades pullback_atr*ATR into the fast EMA (long: low <=
       EMA + pullback_atr*ATR) without closing beyond the slow line.
    3. Confirmation: within `confirm_bars` bars a bar closes above the pullback bar's high
       (long) - a mechanical stand-in for the engulfing / rejection-close triggers the source
       lists without quantifying. The default of 1 is the original rule: the very next bar,
       and nothing later. Above 1 the pullback bar arms the setup and the confirmation may
       arrive on any of the next `confirm_bars` bars - the tolerance for a rejection candle
       that takes an extra bar or two to show itself, which is what a mechanical one-bar
       window silently misses.
    4. Stop: beyond the pullback bar's low by 0.25*ATR (source: below the entry wick
       or the recent swing low, with no published buffer). Target: tp_r * stop.
    """
    n = bars.n
    pip = bars.pip()

    def ema(series: np.ndarray, period: int) -> np.ndarray:
        a = 2 / (period + 1)
        out = np.empty_like(series)
        out[0] = series[0]
        for i in range(1, series.size):
            out[i] = a * series[i] + (1 - a) * out[i - 1]
        return out

    def sma(series: np.ndarray, period: int) -> np.ndarray:
        kern = np.ones(period) / period
        rolled = np.convolve(series, kern, mode="valid")
        return np.concatenate((np.full(period - 1, rolled[0] if rolled.size else 0.0), rolled))

    ef, es = ema(bars.close, fast), sma(bars.close, slow)
    atr = _atr(bars, 14)
    spread = bars.spread_pips()
    sig_i: list[int] = []
    side_l: list[int] = []
    stops_l: list[float] = []
    pb_low = pb_high = np.nan  # pullback bar extremes awaiting confirmation
    pb_dir = 0
    pb_left = 0  # confirmation bars still available to the armed pullback

    for i in range(1, n - 1):
        a = float(atr[i])
        if a <= 0:
            continue
        up = ef[i] > es[i]
        c = float(bars.close[i])
        h, lo = float(bars.high[i]), float(bars.low[i])
        if pb_dir == 1 and c > pb_high:
            # Long: stop below the pullback bar's LOW by 0.25*ATR (researcher rule).
            stop_pips = (bars.open_[i + 1] - pb_low + 0.25 * a) / pip
            sig_i.append(i); side_l.append(1); stops_l.append(max(stop_pips, 0))
            pb_dir = pb_left = 0
            continue
        if pb_dir == -1 and c < pb_low:
            # Short: stop above the pullback bar's HIGH by 0.25*ATR.
            stop_pips = (pb_high - bars.open_[i + 1] + 0.25 * a) / pip
            sig_i.append(i); side_l.append(-1); stops_l.append(max(stop_pips, 0))
            pb_dir = pb_left = 0
            continue
        if pb_dir != 0:
            pb_left -= 1
            if pb_left > 0:
                continue  # the armed pullback still owns this bar
            pb_dir = 0  # window spent: fall through and allow a fresh setup here
        if up and lo <= ef[i] + pullback_atr * a and c > es[i]:
            pb_dir, pb_low, pb_high, pb_left = 1, lo, h, confirm_bars
        elif (not up) and h >= ef[i] - pullback_atr * a and c < es[i]:
            pb_dir, pb_low, pb_high, pb_left = -1, lo, h, confirm_bars

    sig = np.array(sig_i, dtype=np.int64)
    sd = np.array(side_l, dtype=np.int64)
    stops = np.array(stops_l, dtype="float64") if stops_l else np.zeros(0)
    sig, sd, stops, tps = _cost_adjust(
        sig, sd, stops, np.full(sig.size, tp_r), spread, stop_mode, cost_mult
    )
    return sig, sd, stops, tps


def orb_london(
    bars: Bars,
    range_minutes: float = ORB_RANGE_MINUTES,
    trigger_atr: float = 0.25,
    tp_r: float = 2.0,
    stop_ref: str = "atr",
    scan_minutes: float = ORB_SCAN_MINUTES,
    stop_mode: str = "filter",
    cost_mult: float = MIN_STOP_SPREAD_MULT,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """London opening-range breakout scalp (UTC 07:00 start).

    1. Range: high/low of the first `range_minutes` of the 07:00 UTC hour (FXOpen:
       5/15/30-minute opening ranges). Given in minutes and converted with this series'
       own bar interval, so it is a 30-minute range on M5 and on M15 alike. A range
       shorter than one bar cannot be formed at all - an hourly candle does not record
       "the first 30 minutes of the London hour" - so on H1 this rule refuses and returns
       nothing instead of quietly trading a six-hour box as if it were the opening range.
    2. Trigger: a completed bar CLOSES beyond the range edge by trigger_atr*ATR
       measured from the edge. A wick alone is a test, not a breakout (FXOpen).
       One signal per day per direction, inside `scan_minutes` of the range.
    3. Stop: `stop_ref="atr"` = 1*ATR from entry (FXOpen's "just beyond the broken
       edge" spirit); `stop_ref="opposite"` = the opposing range edge (FXOpen's
       2024 session guide). The sources contradict each other here, so the sweep
       tests both. Target: tp_r * stop, at the 2:1-3:1 the sources cite.
    """
    if stop_ref not in ("atr", "opposite"):
        raise ValueError(f"stop_ref must be 'atr' or 'opposite', got {stop_ref!r}")
    n = bars.n
    hour = _utc_hours(bars)
    day = _utc_days(bars)
    atr = _atr(bars, 14)
    spread = bars.spread_pips()
    pip = bars.pip()
    bm = bar_minutes(bars)
    if range_minutes < bm - 1e-9:
        # The range is shorter than one bar: nothing here can measure it.
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64), np.zeros(0), np.zeros(0)
    span = bars_for(range_minutes, bm)
    scan = bars_for(scan_minutes, bm)
    sig_i: list[int] = []
    side_l: list[int] = []
    stops_l: list[float] = []
    i = 0
    while i < n:
        if int(hour[i]) != 7:
            i += 1
            continue
        d = int(day[i])
        end = i + span
        if end >= n or int(day[end]) != d:
            break  # truncated day at data end
        rng_hi = float(bars.high[i:end].max())
        rng_lo = float(bars.low[i:end].min())
        used_long = used_short = False
        for j in range(end, min(n, end + scan)):  # same session day only
            if int(day[j]) != d:
                break
            a = float(atr[j])
            if a <= 0:
                continue
            c = float(bars.close[j])
            o = float(bars.open_[j])
            if not used_long and c > rng_hi + trigger_atr * a:
                if stop_ref == "opposite":
                    stop_pips = max(o - rng_lo, 0.0) / pip
                else:
                    stop_pips = a / pip
                sig_i.append(j); side_l.append(1); stops_l.append(stop_pips)
                used_long = True
            elif not used_short and c < rng_lo - trigger_atr * a:
                if stop_ref == "opposite":
                    stop_pips = max(rng_hi - o, 0.0) / pip
                else:
                    stop_pips = a / pip
                sig_i.append(j); side_l.append(-1); stops_l.append(stop_pips)
                used_short = True
        i = end

    sig = np.array(sig_i, dtype=np.int64)
    sd = np.array(side_l, dtype=np.int64)
    stops = np.array(stops_l, dtype="float64") if stops_l else np.zeros(0)
    sig, sd, stops, tps = _cost_adjust(
        sig, sd, stops, np.full(sig.size, tp_r), spread, stop_mode, cost_mult
    )
    return sig, sd, stops, tps


def asian_fade(
    bars: Bars,
    fade_atr: float = 0.5,
    target: str = "mid",
    confirm_bars: int = 1,
    fade_minutes: float = ASIAN_FADE_MINUTES,
    stop_mode: str = "filter",
    cost_mult: float = MIN_STOP_SPREAD_MULT,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Asian-session range fade (UTC 00:00-06:00 range; fade after 06:00).

    1. Range: high/low of bars in the 00:00-06:00 UTC window. Caveat: sources use
       00:00-04:00, 00:00-08:00, 21:00-06:00 and 21:00-08:00 - there is no single
       agreed window, so this is one cited variant (FXNX's 2-4h Asian range at the
       00:00 end of the spread of definitions).
    2. Fade trigger after 06:00: a bar trades fade_atr*ATR beyond the range edge
       AND closes back inside the range -> fade that direction, enter next open
       (FXNX: enter opposite only after a candle closes back inside). The trigger is
       only looked for in the `fade_minutes` after the range sets - the fade is a
       reaction to the session break, not something still being hunted at midnight.
       `confirm_bars` bounds who may complete that reclaim: the default of 1 keeps the
       original same-bar reading, and above 1 the excursion arms the setup and any of
       the next `confirm_bars` bars may close back inside, at the same frozen range
       edges. The false break and the reclaim are one event; a strict single-bar window
       just means we only notice it when both halves happen to fit in one candle.
    3. Stop: beyond the excursion extreme by 0.25*ATR (FXNX: just beyond the
       false-break wick; no numeric buffer given).
    4. Target: `target="mid"` = range midpoint; `target="far"` = the opposite range
       edge, which is FXNX's stated first target. tp_r is derived, not assumed.
    """
    if target not in ("mid", "far"):
        raise ValueError(f"target must be 'mid' or 'far', got {target!r}")
    n = bars.n
    hour = _utc_hours(bars)
    day = _utc_days(bars)
    atr = _atr(bars, 14)
    spread = bars.spread_pips()
    pip = bars.pip()
    fade_bars = bars_for(fade_minutes, bar_minutes(bars))

    sig_i: list[int] = []
    side_l: list[int] = []
    stops_l: list[float] = []
    tp_pips_l: list[float] = []  # distance to the range midpoint, in pips
    i = 0
    while i < n:
        if int(hour[i]) != 0:
            i += 1
            continue
        d = int(day[i])
        end = i
        while end < n and int(day[end]) == d and int(hour[end]) < 6:
            end += 1
        if end >= n:
            break
        rng_hi = float(bars.high[i:end].max())
        rng_lo = float(bars.low[i:end].min())
        mid = (rng_hi + rng_lo) / 2
        fade_end = min(n, end + fade_bars)  # fade window after the range sets
        armed = 0    # +1: a low excursion is waiting to close back inside, -1: a high one
        arm_j = -1
        for j in range(end, fade_end):
            if int(day[j]) != d:
                break
            a = float(atr[j])
            if a <= 0:
                continue
            h, lo, c = float(bars.high[j]), float(bars.low[j]), float(bars.close[j])
            entry = float(bars.open_[j + 1]) if j + 1 < n else np.nan
            if np.isnan(entry):
                continue
            long_sweep = lo < rng_lo - fade_atr * a
            short_sweep = h > rng_hi + fade_atr * a
            long_ok = long_sweep and c > rng_lo   # false break low -> long
            short_ok = short_sweep and c < rng_hi  # false break high -> short
            if not long_ok and not short_ok and armed != 0 and confirm_bars > 1:
                # An earlier excursion is still waiting to be reclaimed at these same edges.
                reclaimed = c > rng_lo if armed > 0 else c < rng_hi
                if reclaimed:
                    long_ok, short_ok, armed = armed > 0, armed < 0, 0
                elif j - arm_j >= confirm_bars:
                    armed = 0  # window spent; this bar may arm a fresh excursion below
                else:
                    continue
            if long_ok:
                stop_pips = (entry - rng_lo + 0.25 * a) / pip
                target_px = rng_hi if target == "far" else mid
                tp_dist_pips = (target_px - entry) / pip
                if stop_pips > 0 and tp_dist_pips > 0:
                    sig_i.append(j); side_l.append(1)
                    stops_l.append(stop_pips); tp_pips_l.append(tp_dist_pips)
            elif short_ok:
                stop_pips = (rng_hi - entry + 0.25 * a) / pip
                target_px = rng_lo if target == "far" else mid
                tp_dist_pips = (entry - target_px) / pip
                if stop_pips > 0 and tp_dist_pips > 0:
                    sig_i.append(j); side_l.append(-1)
                    stops_l.append(stop_pips); tp_pips_l.append(tp_dist_pips)
            elif confirm_bars > 1:
                # Nothing reclaimed on this bar: arm whichever edge was swept.
                if long_sweep:
                    armed, arm_j = 1, j
                elif short_sweep:
                    armed, arm_j = -1, j
        i = end

    sig = np.array(sig_i, dtype=np.int64)
    sd = np.array(side_l, dtype=np.int64)
    stops = np.array(stops_l, dtype="float64") if stops_l else np.zeros(0)
    tp_pips = np.array(tp_pips_l, dtype="float64") if tp_pips_l else np.zeros(0)
    # The target is a price (the range midpoint), so the R multiple must be recomputed
    # after any stop adjustment: tp_r = distance-to-mid / (final stop). Passing the
    # distance as tp_r keeps every array on the same side of the cost screen.
    sig, sd, stops, tp_pips = _cost_adjust(
        sig, sd, stops, tp_pips, spread, stop_mode, cost_mult
    )
    return sig, sd, stops, tp_pips / stops


def cost_hurdle(
    bars: Bars,
    fn: ScalpFn,
    cost_mult: float = MIN_STOP_SPREAD_MULT,
    **params: Any,
) -> dict[str, Any]:
    """Measure what the cost rule removes: raw signals vs signals that survived.

    Runs the family with the rule off (raw) and on (filter) using the same parameters,
    so the difference is exactly the cost screen. Reported per run so the dashboard can
    show where a template's stops sit relative to the measured spread, and what share of
    the risk the round-trip cost would take at the median stop.
    """
    # The swept params may already carry a stop_mode; the measurement owns it.
    base = {k: v for k, v in params.items() if k != "stop_mode"}
    raw = fn(bars, stop_mode="off", cost_mult=cost_mult, **base)
    kept = fn(bars, stop_mode="filter", cost_mult=cost_mult, **base)
    raw_n, kept_n = int(raw[0].size), int(kept[0].size)
    spread_med = float(np.median(bars.spread_pips())) if bars.n else 0.0
    raw_stops = np.asarray(raw[2], dtype="float64")
    median_stop = round(float(np.median(raw_stops)), 2) if raw_n else None
    return {
        "raw_signals": raw_n,
        "kept_signals": kept_n,
        "kept_share": round(kept_n / raw_n, 3) if raw_n else None,
        "median_stop_pips": median_stop,
        "min_stop_pips": round(float(raw_stops.min()), 2) if raw_n else None,
        "spread_pips_median": round(spread_med, 2),
        "floor_pips": round(float(min(cost_mult * spread_med, 1e9)), 2),
        "cost_mult": cost_mult,
        # Round-trip cost (entry + exit, one spread each) as a fraction of the median stop.
        "cost_share_of_median_stop": (
            round(float(2 * spread_med / median_stop), 3)
            if median_stop is not None and median_stop > 0
            else None
        ),
    }


SCALP_FAMILIES: list[Family] = [
    Family(
        id="sr_flip",
        description="S/R zone break-retest continuation: fractal zone, close beyond, retest, "
        "stop 0.5 ATR past the retest extreme (FXOpen/MultiBank template)",
        fn=sr_flip,
    ),
    Family(
        id="pullback_scalp",
        description="Retracement scalp: slow SMA bias + fast EMA value line, confirmation "
        "close, stop 0.25 ATR past the pullback low (ACY template)",
        fn=pullback_scalp,
    ),
    Family(
        id="orb_london",
        description="London 07:00 UTC opening-range breakout: close beyond the edge, stop "
        "1 ATR or the opposing edge (FXOpen template)",
        fn=orb_london,
    ),
    Family(
        id="asian_fade",
        description="Asian 00:00-06:00 UTC range false-break fade, midpoint or far-edge "
        "target (FXNX template)",
        fn=asian_fade,
    ),
]
