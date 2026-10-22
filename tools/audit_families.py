"""Audit every strategy family against what its own description claims, on real bars.

The question this answers is not "did it make money" (the sweep and the gate do that) but
"is the thing the code computes the thing the docstring says". Reading a strategy tells you
what it was meant to do; only running it tells you what it does. So each family is asked for
its signals on real H1 and M5 history and the shape of those signals is measured:

  * how many signals, and how many survive the one-position-at-a-time rule
  * the clock time of the signals, against the session window the docstring names
  * the stop in pips against the quoted spread, since a stop inside the spread is a coin flip
    that pays the house
  * the exit mix (target / stop / time) and the mean R net of measured costs
  * how many signals were decided by a swing level older than the 400-bar staleness limit
  * the bar-count constants converted into real time at that timeframe

Usage: python tools/audit_families.py [H1 M5 ...]
"""

from __future__ import annotations

import sys
from collections import Counter
from typing import Any

import numpy as np

from mt5_intel.config import load_settings
from mt5_intel.mt5_adapter import ReadOnlyMT5
from mt5_intel.research import strategies as S
from mt5_intel.research import strategies_scalp as SS
from mt5_intel.research.backtest import (
    Bars,
    load_bars,
    one_at_a_time,
    plan_trade,
    resolve_offset_s,
    resolve_outcome,
    summarize,
)
from mt5_intel.research.replay import DEFAULT_MAX_HOLD, SCALP_TIMEFRAMES, families_for

BARS = 20_000


def hours_of(bars: Bars, idx: np.ndarray) -> Counter[int]:
    """UTC hour of each signal bar.

    On the UTC clock, not the broker's: the session rules name UTC hours, so a histogram drawn on
    the raw stamp would show a session rule firing three hours away from its own window on a UTC+3
    terminal and look like a bug when it is the only honest reading - and vice versa.
    """
    return Counter(int((int(bars.utc_time()[i]) // 3600) % 24) for i in idx)


def histogram(counts: Counter[int]) -> str:
    """A 24-cell row so an off-window signal is visible at a glance."""
    peak = max(counts.values()) if counts else 1
    ramp = " .:-=+*#%@"
    return "".join(
        ramp[min(len(ramp) - 1, int(counts.get(h, 0) / (peak or 1) * (len(ramp) - 1)))]
        for h in range(24)
    )


def bar_minutes(timeframe: str) -> int:
    return {"M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}[timeframe]


def grade(bars: Bars, family: dict[str, Any], max_hold: int = DEFAULT_MAX_HOLD) -> dict[str, Any]:
    """One family's raw output, the trades it actually takes, and how they ended."""
    sig_i, side, stops, tp_r = (np.asarray(a) for a in family["fn"](bars))
    taken = one_at_a_time(bars, sig_i, side, stops, tp_r, max_hold)
    outcomes = []
    for k, _, _ in taken:
        i = int(sig_i[k])
        if float(stops[k]) <= 0:
            continue
        plan = plan_trade(bars, i, i + 1, int(side[k]), float(stops[k]), float(tp_r[k]))
        out = resolve_outcome(bars, plan, i, int(side[k]), max_hold) if plan else None
        if out is not None:
            outcomes.append(out)
    summary = summarize(outcomes)
    return {
        "signals": int(sig_i.size),
        "taken": len(taken),
        "sig_i": sig_i,
        "stops": stops,
        "summary": summary,
        "outcomes": outcomes,
    }


def report_family(bars: Bars, timeframe: str, family: dict[str, Any]) -> None:
    name = str(family["id"])
    try:
        res = grade(bars, family)
    except Exception as exc:  # noqa: BLE001 - a family that raises is itself the finding
        print(f"  {name:<20} RAISED {type(exc).__name__}: {exc}")
        return

    sig_i = res["sig_i"]
    stops = np.asarray(res["stops"], dtype="float64")
    spread = float(np.median(bars.spread_pips())) if bars.n else float("nan")
    sig_med = float(np.median(stops)) if stops.size else float("nan")
    hours = histogram(hours_of(bars, sig_i))
    s = res["summary"]
    net = s["net_r_mean"]
    per_1k = res["signals"] / max(1, bars.n) * 1000

    print(f"  {name:<20} {res['signals']:>6} raw {res['taken']:>6} taken ({per_1k:5.1f}/1k bars)")
    print(f"    {'':<18} stop {sig_med:6.2f} pips vs spread {spread:.2f} "
          f"(x{sig_med / spread if spread else float('nan'):5.1f})")
    print(f"    {'':<18} hours UTC {hours}")
    if s["n"]:
        print(f"    {'':<18} n={s['n']:<5} mean {net:+.3f}R  t={s['t_stat']}  "
              f"win {s['win_rate']:.0%}  stop-hit {s['touched_sl_rate']:.0%}  "
              f"hold {s['median_hold_bars']} bars")
    else:
        print(f"    {'':<18} no gradeable trades")


def report_windows(bars: Bars, timeframe: str) -> None:
    """The session rules' windows in real time at this timeframe.

    These were bar counts written for M5 and are now durations converted with the series' own bar
    interval, so this line is the check: 30 minutes has to read as 30 minutes on every timeframe,
    and a range that cannot be measured in whole bars has to be refused rather than widened.
    """
    measured = SS.bar_minutes(bars)
    span = SS.ORB_RANGE_MINUTES / measured
    fade = SS.bars_for(SS.ASIAN_FADE_MINUTES, measured)
    scan = SS.bars_for(SS.ORB_SCAN_MINUTES, measured)
    forms = SS.ORB_RANGE_MINUTES >= measured - 1e-9
    print(f"  {timeframe} bar interval: {measured:g} min")
    print(f"    orb range {SS.ORB_RANGE_MINUTES:g} min = {span:g} bars, "
          f"scan {SS.ORB_SCAN_MINUTES:g} min = {scan} bars"
          + ("" if forms else "  -> shorter than one bar: the rule refuses"))
    print(f"    asian fade window {SS.ASIAN_FADE_MINUTES:g} min = {fade} bars "
          f"(six fixed bars of the 00:00-06:00 UTC range)")
    print(f"    sr_flip retest is still a bar count (6 bars = {6 * measured:g} min here), "
          f"as its docstring says")
    print(f"    in SCALP_TIMEFRAMES: {timeframe in SCALP_TIMEFRAMES}")


def report_stale_levels(bars: Bars) -> None:
    """How often sweep_reclaim acts on a swing level older than its own 400-bar limit."""
    n = bars.n
    k = 5
    sl_low, sl_high = S._swing_lows(bars, k), S._swing_highs(bars, k)
    low_val = np.full(n, np.nan)
    low_age = np.full(n, -1)
    high_val = np.full(n, np.nan)
    high_age = np.full(n, -1)
    cur_v, cur_i = np.nan, -1
    cur_hv, cur_hi = np.nan, -1
    for i in range(n):
        if sl_low[i]:
            cur_v, cur_i = bars.low[i - k], i - k
        if sl_high[i]:
            cur_hv, cur_hi = bars.high[i - k], i - k
        low_val[i], low_age[i] = cur_v, i - cur_i
        high_val[i], high_age[i] = cur_hv, i - cur_hi

    sig_i, side, _, _ = S.sweep_and_reclaim(bars)
    stale = 0
    for kk, i in enumerate(sig_i):
        i = int(i)
        age = low_age[i] if int(side[kk]) > 0 else high_age[i]
        if age > 400:
            stale += 1
    over_limit = int(np.sum((low_age > 400) & (high_age <= 400))) + int(
        np.sum((high_age > 400) & (low_age <= 400))
    )
    print(f"  sweep_reclaim: {sig_i.size} signals, {stale} decided by a level older than 400 bars")
    print(f"    bars where exactly one side's level is older than 400: {over_limit} "
          f"({over_limit / max(1, n):.1%})")
    print("    the guard now skips a bar when EITHER side is stale; it used to need both")


def report_rsi_variants(bars: Bars) -> None:
    """One RSI in the project now. This is the gap the old second implementation left.

    `range_extreme_mr` used to build its own reading from a simple mean of gains and losses while
    every other family and the panel used Wilder's, so this is the size of the disagreement it was
    trading on - kept as a measurement, not as a warning, because the two are no longer both live.
    """
    deltas = np.diff(bars.close, prepend=bars.close[0])
    up = np.where(deltas > 0, deltas, 0.0)
    dn = np.where(deltas < 0, -deltas, 0.0)
    au, ad = S._causal_mean(up, 14), S._causal_mean(dn, 14)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.where(ad > 0, au / ad, np.inf)
    sma_rsi = 100 - 100 / (1 + rs)
    wilder = S._wilder_rsi(bars.close, 14)
    both = np.isfinite(sma_rsi) & np.isfinite(wilder)
    diff = np.abs(sma_rsi[both] - wilder[both])
    print("  RSI: what the family used to trade (SMA gains/losses) vs Wilder RSI(14), now the only")
    print(f"    median |diff| {np.median(diff):.2f} pts, 90th {np.percentile(diff, 90):.2f}, "
          f"max {diff.max():.2f} pts")
    at_65 = int(np.sum((sma_rsi[both] > 65) != (wilder[both] > 65)))
    at_35 = int(np.sum((sma_rsi[both] < 35) != (wilder[both] < 35)))
    print(f"    bars where the two disagreed about the 65 line: {at_65}; "
          f"about the 35 line: {at_35}")


def main() -> int:
    timeframes = sys.argv[1:] or ["H1", "M5"]
    settings = load_settings()
    import MetaTrader5 as backend

    mt5 = ReadOnlyMT5(backend)
    mt5.connect(
        login=settings.login,
        password=settings.password,
        server=settings.server,
        terminal_path=settings.terminal_path,
    )
    failures = 0
    try:
        for timeframe in timeframes:
            offset = resolve_offset_s(mt5, settings, settings.symbol_root)
            bars = load_bars(
                mt5, settings.symbol_root, timeframe, BARS, utc_offset_s=offset
            )
            if bars is None:
                print(f"{timeframe}: no history")
                failures += 1
                continue
            print(f"\n=== {settings.symbol_root} {timeframe}, {bars.n} bars ===")
            clock = "read" if offset is not None else "UNKNOWN - session rules refuse"
            print(f"  broker clock offset: {offset} s from UTC ({clock})")
            report_windows(bars, timeframe)
            print("  --- families the console runs on this timeframe ---")
            for family in families_for(timeframe):
                report_family(bars, timeframe, family)
            print("  --- implementation checks ---")
            report_stale_levels(bars)
            report_rsi_variants(bars)
    finally:
        mt5.shutdown()
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
