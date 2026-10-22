"""What every strategy is in right now: the position it took, and whether it is still running.

A replay answers *"what would these rules have done on old bars, and would that have paid?"*
This answers the question a running system invites every time you look at the chart: what is each
rule in now, at what entry, stop and target, and is that trade still open or already finished?

It reads positions, not signals. A rule has no memory of being in a trade: while its condition
holds it re-fires on every closed bar, so its newest raw signal is usually its own running
position restated - and 55-85% of most families' signals are exactly that (D-035). Showing the
newest signal is what made this view say "just entered" on every bar while the rule sat one
position deep, further into a loss each time. Each row is read through `one_at_a_time`, so it
shows the trade as taken, and it reports how many re-fires it stood aside for rather than hiding
them.

The distinction matters because the trend families only speak at the *start* of a move: a
crossover fires on the bar the averages trade places, time-series momentum fires when the sign
of the trailing return flips, and a pullback rule fires while price is back at its average.
Days later the trend is still there and the rule has nothing new to say, so a page built only
from replayed cards looks silent exactly when the market is most obviously trending.

Nothing here is a recommendation, and nothing places an order. The card keeps the research
convention: last closed bar for the signal, next bar's open for the entry, a fixed stop and a
fixed R target decided before entry.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import numpy as np

from .backtest import (
    CLOSED_SL,
    CLOSED_TIME,
    CLOSED_TP,
    STILL_OPEN,
    Bars,
    bars_held,
    closed_r,
    one_at_a_time,
    plan_trade,
    refires_while_open,
    without_forming_bar,
)

# The same family list the replay uses, so the live view and the graded what-if can never
# disagree about which rules exist or which of them apply to a timeframe.
from .replay import DEFAULT_MAX_HOLD, families_for
from .strategies import Family, _atr, _ema, _sma, _wilder_rsi

# How the newest card stands as of the newest bar.
STATUS_OPEN = "open"            # entry taken, no barrier reached yet: this is the live one
STATUS_TARGET = "target"        # the target was reached first
STATUS_STOP = "stop"            # the stop was reached first (D-012: stop first on a double touch)
STATUS_TIME = "time exit"       # neither barrier inside the hold limit
STATUS_NONE = "none"            # this rule has produced no card over the bars it was given
STATUS_ERROR = "error"          # the rule raised; reported, never fatal

# The windows the backdrop is read at. Named here because the chart export draws these same
# averages as lines: a second copy of "50" in the exporter could drift from the number the
# panel quotes, and then the chart line and the card would disagree with no way to tell which
# of the two the rules actually used.
CONTEXT_EMA_FAST = 50
CONTEXT_EMA_SLOW = 200
CONTEXT_SMA_FAST = 20
CONTEXT_SMA_SLOW = 50
CONTEXT_ATR_PERIOD = 14
CONTEXT_RSI_PERIOD = 14
CONTEXT_RANGE_BARS = 96
CONTEXT_CHANGES = (24, 96, 240)


def _utc(epoch: int, offset_s: int) -> str:
    """Bar stamps carry the broker's clock; report the instant in UTC when the offset is known."""
    return datetime.fromtimestamp(int(epoch) - offset_s, UTC).strftime("%Y-%m-%d %H:%M")


# The count of bars on each side of a bar's low for it to count as a swing low at all, named here
# because the live row reports the levels it read - so a reader auditing a card against the chart
# has to be able to reproduce the read with the same number. This is the same definition the
# take-profit study ran against (tools/tp_structure_study.py): the most recent swing whose
# confirming handful of bars had all closed by the signal bar, so the level was knowable when the
# rule chose it, not one a later bar could have hindsight-drawn.
SWING_PIVOT_L = 5


def known_swings(bars: Bars, i: int, pivot_l: int = SWING_PIVOT_L) -> tuple[float | None, float | None]:
    """The previous swing low and high the market had already confirmed by bar `i`.

    For a sell, the previous lower-low is where the account holder expects the fall to stall - a
    supportive low whose confirming bars had all closed before the signal bar, so the level was
    knowable when the rule chose it. This is the read the take-profit study measured against
    (tools/tp_structure_study.py), and what the auto trader reads when a strategy's target mode is
    set to "previous swing": the artifact carries the levels, the engine only places the order.

    A swing is a bar whose low (or high) is the strict extreme of `pivot_l` neighbours on both
    sides, and it counts as known only once those `pivot_l` confirming bars have all closed by
    bar `i`. The nearest such pivot before the signal bar is the one returned; None when history
    holds none (the first bars of the window, or a bar too early in a one-sided move), which the
    reader is told to take as "no structural level, fall back to the fixed target".
    """
    low = high = None
    # Scan backward from the newest bar that could still have finished confirming by i, so the
    # nearest known pivot is found first and the scan stops there.
    for p in range(min(bars.n - pivot_l - 1, i) - pivot_l, pivot_l - 1, -1):
        if p + pivot_l > i:
            continue
        window = slice(p - pivot_l, p + pivot_l + 1)
        if low is None and float(bars.low[window].min()) == float(bars.low[p]):
            low = float(bars.low[p])
        if high is None and float(bars.high[window].max()) == float(bars.high[p]):
            high = float(bars.high[p])
        if low is not None and high is not None:
            break
    return low, high


def placement_at(bars: Bars, i: int) -> tuple[float | None, bool | None, bool | None]:
    """The range read at bar `i`, word for word as `market_context` measures it at the last
    closed bar.

    The `confluence` fade rule needs the placement of the *signal's own* bar - where the market
    stood when the agreeing rules spoke - and the view's context covers only the last closed
    bar, which the signal bar equals for exactly one bar. A card read a bar later would
    otherwise be faded against the wrong candle's placement: the same mistake
    `dashboard/lib/confluence.ts` already refuses to make when placing a combination, and the
    same answer - measure it where it belongs, on the card's own bar.

    Returns `(range_pos, fresh_high, fresh_low)`: a fraction of the previous 96-bar range, and
    whether the bar itself made a fresh high or low against the 96 bars before it.
    """
    if i < 0 or i >= bars.n:
        return None, None, None
    lo_i = max(0, i - CONTEXT_RANGE_BARS + 1)
    range_low = float(bars.low[lo_i : i + 1].min())
    range_high = float(bars.high[lo_i : i + 1].max())
    prior_high = float(bars.high[max(0, i - CONTEXT_RANGE_BARS) : i].max()) if i > 0 else None
    prior_low = float(bars.low[max(0, i - CONTEXT_RANGE_BARS) : i].min()) if i > 0 else None
    pos = (
        round((float(bars.close[i]) - range_low) / (range_high - range_low), 3)
        if range_high > range_low
        else None
    )
    fresh_high = None if prior_high is None else bool(float(bars.high[i]) >= prior_high)
    fresh_low = None if prior_low is None else bool(float(bars.low[i]) <= prior_low)
    return pos, fresh_high, fresh_low


def latest_setup(
    bars: Bars,
    family: Family,
    max_hold_bars: int = DEFAULT_MAX_HOLD,
    offset_s: int = 0,
) -> dict[str, Any]:
    """The position one rule is in right now, or its newest finished trade.

    Cards are read through `one_at_a_time` (D-035). A rule re-fires on every bar its condition
    holds, so its newest raw signal is usually its own running position restated: showing that
    signal is what made this view read "just entered" on every bar while the rule actually sat
    one position deep, further into a loss each time. The row therefore describes the trade as it
    was taken - first entry, true age, true R - and counts the re-fires it stood aside for.

    The card is *open* when no barrier has been reached and the hold window has not elapsed;
    otherwise it closed, and the row says where and when. Both readings matter: an open card is
    the trade the rule would have you in now, and a closed one is why a rule that is right about
    the trend can still show nothing live.
    """
    row: dict[str, Any] = {
        "family": family["id"],
        # The settings this instance of the rule runs with, when the name leaves room for doubt.
        # A card is read against a chart, and "ma_crossover" is three different pairs in the
        # sweep, so the card says which one it was before the reader tries to match the lines.
        "params": family.get("params"),
        "side": None,
        "status": STATUS_NONE,
        "signal_utc": None,
        "signal_epoch": None,
        "age_s": None,
        "bars_since_signal": None,
        "entry": None,
        "sl": None,
        "tp": None,
        "stop_pips": None,
        "tp_r": None,
        "bars_held": None,
        "r_now": None,
        "r_result": None,
        "r_to_stop": None,
        "r_to_target": None,
        "closed_utc": None,
        "resignals": 0,
        "latest_signal": None,
        "signals": 0,
        "taken": 0,
        "note": None,
        # Where the market stood on this card's own signal bar - the read the `confluence` fade
        # rule fades the agreeing group against. None until the card has a signal bar to place.
        "range_pos": None,
        "fresh_high": None,
        "fresh_low": None,
        # The previous confirmed swings of the signal bar - what a "previous swing" target is
        # placed at. None here reads as "no structural level was known at the signal bar", and
        # the engine falls back to the fixed target for any card under it.
        "swing_low": None,
        "swing_high": None,
    }
    # The rules read CLOSED bars only, for the same reason `market_context` stops one bar short:
    # the history the terminal hands over ends in a candle that is still being built, and a rule
    # asked to grade it fires and un-fires as that candle moves - the on/off card the Live tab
    # was seen flickering with. The forming bar stays in `bars` below for everything that reads
    # a live entry (`plan_trade` on the take-now card, `r_now`), but no signal and no verdict
    # may come from it.
    closed = without_forming_bar(bars)
    try:
        sig_i, side, stops, tp_r = (np.asarray(a) for a in family["fn"](closed))
    except Exception as exc:  # noqa: BLE001 - one broken rule must not blank the whole page
        row["status"] = STATUS_ERROR
        row["note"] = f"{type(exc).__name__}: {exc}"
        return row
    if sig_i.size == 0:
        row["note"] = "no card in the history it was given"
        return row
    row["signals"] = int(sig_i.size)

    taken = one_at_a_time(closed, sig_i, side, stops, tp_r, max_hold_bars)
    # A signal on the very last CLOSED bar has no closed entry bar to walk yet: its entry is the
    # forming candle's open - the price a market order meets now. `one_at_a_time`, walking closed
    # bars only, skipped it, so the live view adds it here as the rule's take-now card: planned
    # on the full bars, and graded only once its entry bar has closed. It still has to fit the
    # one-position rule: only when no trade is running and the slot the last exit left is free.
    if not any(t[1] is None for t in taken):
        last_closed = closed.n - 1
        newest = [j for j in range(int(sig_i.size)) if int(sig_i[j]) == last_closed]
        entry_bar_now = last_closed + 1
        if newest and (not taken or int(taken[-1][1]) < entry_bar_now):
            taken.append((newest[-1], None, STILL_OPEN))
    row["taken"] = len(taken)
    if not taken:
        row["note"] = "no card this rule could take on the bars it was given"
        return row

    # The live one is the position still running; if the rule is flat, the newest closed trade.
    running = next((t for t in reversed(taken) if t[1] is None), None)
    k, exit_bar, kind = running if running is not None else taken[-1]

    digits = bars.digits
    i = int(sig_i[k])
    sd = int(side[k])
    entry_bar = i + 1
    plan = plan_trade(bars, i, entry_bar, sd, float(stops[k]), float(tp_r[k]))
    if plan is None:
        row["note"] = "the newest signal has no bar to enter on yet"
        return row

    entry = float(plan["entry"])
    sl = float(plan["sl"])
    tp = float(plan["tp"])
    stop_dist = abs(sl - entry)
    cost_r = float(plan["cost_r"])
    stop_pips = float(stops[k])
    target_r = float(tp_r[k])

    status = {
        CLOSED_TP: STATUS_TARGET,
        CLOSED_SL: STATUS_STOP,
        CLOSED_TIME: STATUS_TIME,
        STILL_OPEN: STATUS_OPEN,
    }[kind]
    # A running trade is as old as the bars since its entry; a closed one ended where it ended.
    held = bars_held(entry_bar, exit_bar, bars.n)

    px = float(bars.close[-1])
    # Signed distance from entry in R, whichever side this is: +1R is the stop, +target_r the
    # target, so the two barriers are two subtractions away rather than a per-side branch.
    move_r = ((px - entry) if sd > 0 else (entry - px)) / stop_dist
    r_result = closed_r(
        kind,
        target_r=target_r,
        cost_r=cost_r,
        entry=entry,
        side=sd,
        stop_dist=stop_dist,
        exit_price=None if exit_bar is None else float(bars.close[int(exit_bar)]),
    )

    # How many times this rule asked to enter again while this one position was running: the
    # signals it stood aside for. Shown rather than absorbed, because a re-fire per bar is the
    # difference between "one trade" and "one trade repeated thirty times".
    live_until = bars.n - 1 if exit_bar is None else int(exit_bar)
    resignals = refires_while_open(sig_i, k, entry_bar, live_until)
    pos, fresh_high, fresh_low = placement_at(bars, i)
    # The swings the signal bar had already confirmed: written into the row so the auto trader
    # can place a "previous swing" target from the artifact alone, and the panel can show where
    # that target would sit without re-deriving it from the chart.
    swing_low, swing_high = known_swings(bars, i)

    # Newest signal while the position is still open: when a strategy is held open for up to
    # max_hold_bars, it may re-fire on a newer bar (same or opposite side). Surface the newest
    # signal's plan so the dashboard and chart can show what the rule is signalling right now
    # without waiting for the old trade to hit TP/SL/time-exit.
    latest_sig: dict[str, Any] | None = None
    if exit_bar is None and resignals > 0:
        later_indices = [
            j for j in range(k + 1, int(sig_i.size)) if entry_bar < int(sig_i[j]) + 1 <= live_until
        ]
        if later_indices:
            jk = later_indices[-1]
            ji = int(sig_i[jk])
            jsd = int(side[jk])
            j_entry_bar = ji + 1
            j_plan = plan_trade(bars, ji, j_entry_bar, jsd, float(stops[jk]), float(tp_r[jk]))
            if j_plan is not None:
                j_entry = float(j_plan["entry"])
                j_sl = float(j_plan["sl"])
                j_tp = float(j_plan["tp"])
                j_stop_dist = abs(j_sl - j_entry)
                j_cost_r = float(j_plan["cost_r"])
                j_move_r = ((px - j_entry) if jsd > 0 else (j_entry - px)) / j_stop_dist
                j_held = bars_held(j_entry_bar, None, bars.n)
                j_pos, j_fresh_high, j_fresh_low = placement_at(bars, ji)
                j_swing_low, j_swing_high = known_swings(bars, ji)
                latest_sig = {
                    "side": "buy" if jsd > 0 else "sell",
                    "signal_utc": _utc(int(bars.time[ji]), offset_s),
                    "signal_epoch": int(bars.time[ji]),
                    "age_s": int(bars.time[-1]) - int(bars.time[ji]),
                    "bars_since_signal": int(bars.n - 1 - ji),
                    "entry": round(j_entry, digits),
                    "sl": round(j_sl, digits),
                    "tp": round(j_tp, digits),
                    "stop_pips": round(float(stops[jk]), 1),
                    "tp_r": round(float(tp_r[jk]), 1),
                    "bars_held": j_held,
                    "r_now": round(j_move_r - j_cost_r, 3),
                    # The fade rule reads this signal's own bar, not the held trade's.
                    "range_pos": j_pos,
                    "fresh_high": j_fresh_high,
                    "fresh_low": j_fresh_low,
                    # The same swings, of the signal's own bar rather than the held trade's:
                    # a later signal names its own structure, not the position's.
                    "swing_low": j_swing_low,
                    "swing_high": j_swing_high,
                }

    row.update(
        {
            "side": "buy" if sd > 0 else "sell",
            "status": status,
            "signal_utc": _utc(int(bars.time[i]), offset_s),
            "signal_epoch": int(bars.time[i]),
            "age_s": int(bars.time[-1]) - int(bars.time[i]),
            "bars_since_signal": int(bars.n - 1 - i),
            "entry": round(entry, digits),
            "sl": round(sl, digits),
            "tp": round(tp, digits),
            "stop_pips": round(stop_pips, 1),
            "tp_r": round(target_r, 1),
            "bars_held": held,
            "r_now": None if exit_bar is not None else round(move_r - cost_r, 3),
            "r_result": None if r_result is None else round(r_result, 3),
            "r_to_stop": round(move_r + 1.0, 3),
            "r_to_target": round(target_r - move_r, 3),
            "closed_utc": None if exit_bar is None else _utc(int(bars.time[int(exit_bar)]), offset_s),
            "resignals": resignals,
            "latest_signal": latest_sig,
            "range_pos": pos,
            "fresh_high": fresh_high,
            "fresh_low": fresh_low,
            "swing_low": None if swing_low is None else round(swing_low, digits),
            "swing_high": None if swing_high is None else round(swing_high, digits),
        }
    )
    return row


def market_context(bars: Bars, offset_s: int = 0) -> dict[str, Any]:
    """The backdrop the cards are read against, taken from closed bars only.

    The last bar from the terminal is still forming, so every reading here stops one bar short
    of the end: a half-built candle must not move a moving average the rules have not seen.
    """
    n = bars.n
    i = max(0, n - 2)
    digits = bars.digits
    pip = bars.pip()
    close = float(bars.close[i])
    atr = float(_atr(bars, CONTEXT_ATR_PERIOD)[i])
    e_fast = _ema(bars.close, CONTEXT_EMA_FAST)
    e_slow = _ema(bars.close, CONTEXT_EMA_SLOW)
    s_fast = _sma(bars.close, CONTEXT_SMA_FAST)
    s_slow = _sma(bars.close, CONTEXT_SMA_SLOW)

    def value(series: np.ndarray) -> float | None:
        v = float(series[i])
        return round(v, digits) if np.isfinite(v) else None

    changes: dict[str, float | None] = {}
    for look in CONTEXT_CHANGES:
        changes[str(look)] = (
            round((close - float(bars.close[i - look])) / pip, 1) if i - look >= 0 else None
        )
    lo_i = max(0, i - CONTEXT_RANGE_BARS + 1)
    range_low = float(bars.low[lo_i : i + 1].min())
    range_high = float(bars.high[lo_i : i + 1].max())
    prior_high = float(bars.high[max(0, i - CONTEXT_RANGE_BARS) : i].max()) if i > 0 else None
    prior_low = float(bars.low[max(0, i - CONTEXT_RANGE_BARS) : i].min()) if i > 0 else None
    rsi = value(_wilder_rsi(bars.close, CONTEXT_RSI_PERIOD))
    ef, es = value(e_fast), value(e_slow)
    return {
        "last_closed_utc": _utc(int(bars.time[i]), offset_s),
        "last_price": round(float(bars.close[-1]), digits),
        "close": round(close, digits),
        "atr_pips": round(atr / pip, 1) if np.isfinite(atr) else None,
        "atr_period": CONTEXT_ATR_PERIOD,
        "ema_fast_period": CONTEXT_EMA_FAST,
        "ema_slow_period": CONTEXT_EMA_SLOW,
        "sma_fast_period": CONTEXT_SMA_FAST,
        "sma_slow_period": CONTEXT_SMA_SLOW,
        "ema_fast": ef,
        "ema_slow": es,
        "ema_trend": None if ef is None or es is None else ("up" if ef > es else "down"),
        "rsi_period": CONTEXT_RSI_PERIOD,
        "rsi": rsi,
        "sma_fast": value(s_fast),
        "sma_slow": value(s_slow),
        "sma_sep_pips": (
            round((float(s_fast[i]) - float(s_slow[i])) / pip, 1)
            if np.isfinite(s_fast[i]) and np.isfinite(s_slow[i])
            else None
        ),
        # Signed: positive means the fast average is the one on top.
        "ema_sep_pips": (
            round((float(e_fast[i]) - float(e_slow[i])) / pip, 1)
            if np.isfinite(e_fast[i]) and np.isfinite(e_slow[i])
            else None
        ),
        "change_pips": changes,
        "range_bars": CONTEXT_RANGE_BARS,
        "range_high": round(range_high, digits),
        "range_low": round(range_low, digits),
        "range_pos": round((close - range_low) / (range_high - range_low), 3)
        if range_high > range_low
        else None,
        "fresh_high": None if prior_high is None else bool(float(bars.high[i]) >= prior_high),
        "fresh_low": None if prior_low is None else bool(float(bars.low[i]) <= prior_low),
    }


def build(
    bars: Bars,
    timeframe: str,
    *,
    symbol: str,
    max_hold_bars: int = DEFAULT_MAX_HOLD,
    offset_s: int = 0,
    now_epoch: int | None = None,
) -> dict[str, Any]:
    """The whole live view: market backdrop, then every family's newest card.

    Live cards come first and the rest follow newest-signal-first, so the top of the page is
    always the thing that is actually running.
    """
    setups = [latest_setup(bars, family, max_hold_bars, offset_s) for family in families_for(timeframe)]
    setups.sort(key=lambda row: (row["status"] != STATUS_OPEN, -(row["signal_epoch"] or 0)))
    context = market_context(bars, offset_s)
    return {
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "symbol": symbol,
        "timeframe": timeframe,
        "clock": "utc" if offset_s else "server",
        "digits": bars.digits,
        "max_hold_bars": max_hold_bars,
        "bars": bars.n,
        "asof_bar_epoch": int(bars.time[-1]),
        "asof_bar_utc": _utc(int(bars.time[-1]), offset_s),
        "open_count": sum(1 for row in setups if row["status"] == STATUS_OPEN),
        "context": context,
        "setups": setups,
    }


def _print(payload: dict[str, Any]) -> None:
    ctx = payload["context"]
    clock = "UTC" if payload["clock"] == "utc" else "server clock"
    print(
        f"{payload['symbol']} {payload['timeframe']} - "
        f"{payload['open_count']} live card(s) of {len(payload['setups'])} strategies "
        f"as of {payload['asof_bar_utc']} ({clock})"
    )
    print(
        f"  close {ctx['close']}  ATR {ctx['atr_pips']} pips  "
        f"EMA{ctx['ema_fast_period']}/{ctx['ema_slow_period']} {ctx['ema_trend']}  "
        f"change 24/96/240 bars {ctx['change_pips']} pips  "
        f"range pos {ctx['range_pos']} fresh high {ctx['fresh_high']}"
    )
    print()
    print(
        f"{'strategy':22s} {'side':4s} {'signal (UTC)':16s} {'entry':>9s} {'stop':>9s} "
        f"{'target':>9s} {'status':>9s} {'re-sig':>6s} {'R':>7s}"
    )
    for row in payload["setups"]:
        r = row["r_now"] if row["status"] == STATUS_OPEN else row["r_result"]
        print(
            f"{row['family']:22s} {row['side'] or '-':4s} {row['signal_utc'] or '-':16s} "
            f"{_cell(row['entry'])} {_cell(row['sl'])} {_cell(row['tp'])} "
            f"{row['status']:>9s} {row['resignals']:6d} {('-' if r is None else f'{r:+.2f}'):>7s}"
        )


def _cell(value: Any) -> str:
    """A right-aligned price cell, or a dash when the card has no price (no card at all)."""
    return f"{'-' if value is None else value!s:>9}"


def main() -> int:
    """Ad-hoc run: print the live view for any timeframe the research harness knows."""
    import argparse

    from ..config import ConfigError, load_settings
    from ..datachecks import estimate_server_offset_s
    from ..mt5_adapter import ReadOnlyMT5
    from .backtest import TIMEFRAMES, load_bars, resolve_offset_s

    parser = argparse.ArgumentParser(description="Live per-strategy cards")
    parser.add_argument("timeframe", nargs="?", default="H1", choices=sorted(TIMEFRAMES))
    parser.add_argument("--bars", type=int, default=6_000)
    parser.add_argument("--max-hold", type=int, default=DEFAULT_MAX_HOLD)
    parser.add_argument("--out", default=None, help="also write the JSON payload here")
    args = parser.parse_args()

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Config error: {exc}")
        return 2
    try:
        import MetaTrader5 as backend
    except ImportError:
        print("MetaTrader5 package not installed (Windows only).")
        return 2

    mt5 = ReadOnlyMT5(backend)
    mt5.connect(
        login=settings.login,
        password=settings.password,
        server=settings.server,
        terminal_path=settings.terminal_path,
    )
    try:
        bars = load_bars(
                mt5,
                settings.symbol_root,
                args.timeframe,
                args.bars,
                utc_offset_s=resolve_offset_s(mt5, settings, settings.symbol_root),
            )
        tick = mt5.symbol_info_tick(settings.symbol_root)
    finally:
        mt5.shutdown()
    if bars is None:
        print("Insufficient history from terminal.")
        return 2
    now_epoch = int(datetime.now(UTC).timestamp())
    if settings.utc_offset_hours is not None:
        offset_s = int(settings.utc_offset_hours * 3600)
    elif tick is not None:
        offset_s = estimate_server_offset_s(int(tick.time), now_epoch) or 0
    else:
        offset_s = 0
    payload = build(
        bars,
        args.timeframe,
        symbol=settings.symbol_root,
        max_hold_bars=args.max_hold,
        offset_s=offset_s,
        now_epoch=now_epoch,
    )
    _print(payload)
    if args.out:
        import json
        from pathlib import Path

        Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
