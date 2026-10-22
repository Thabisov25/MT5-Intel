"""Research core: data loading, costs in R, and a conservative triple-barrier backtester.

Conservative fills (D-012): SL fires first when one bar touches both barriers; shorts
trigger on ask, longs on bid; entry is at the next bar's open after a closed-bar signal;
costs are the measured M5 bar snapshot spread (per design doc §6.10).
All results are in R (multiples of the initial stop distance) — never in "profit factor".
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..config import Settings
from ..datachecks import estimate_server_offset_s, offset_is_plausible
from ..mt5_adapter import ReadOnlyMT5

M1, M5, M15, M30, H1, H4, D1 = 1, 5, 15, 30, 16385, 16388, 16408
TIMEFRAMES: dict[str, int] = {"M5": M5, "M15": M15, "M30": M30, "H1": H1, "H4": H4, "D1": D1}
SAFE_REQUEST_BARS = 80_000  # live probe: 100k is rejected by the terminal


@dataclass
class Bars:
    time: np.ndarray  # int64 epoch seconds, server clock
    open_: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    spread_points: np.ndarray  # i4 snapshot per bar
    point: float
    digits: int
    # How far the stamps above run ahead of UTC. Bar stamps are the broker's clock (D-011), so a
    # rule written as "the 07:00 UTC hour" can only be placed on that clock once this is known.
    # None means nobody has read the broker's clock: the session rules refuse rather than guess.
    utc_offset_s: int | None = None

    @property
    def n(self) -> int:
        return int(self.time.size)

    def utc_time(self) -> np.ndarray:
        """Bar stamps on the UTC clock. The session rules read this, never `time` directly."""
        offset = 0 if self.utc_offset_s is None else int(self.utc_offset_s)
        return self.time - offset

    def pip(self) -> float:
        return self.point * 10 if self.digits in (3, 5) else self.point

    def spread_pips(self) -> np.ndarray:
        return self.spread_points.astype("float64") * self.point / self.pip()


def without_forming_bar(bars: Bars) -> Bars:
    """The same history with the still-forming last bar dropped, for closed-bar-only reads.

    The bars a terminal hands over always end in a candle that is still being built. A rule asked
    to grade it will fire and un-fire as that candle moves, which is the on/off behaviour the
    live view and the chart were seen flickering with; the market context already stopped one
    bar short of it for the same reason. The forming bar is not thrown away: callers keep passing
    the full bars wherever an entry price is read, because its open is the price a market order
    meets right now.
    """
    if bars.n <= 1:
        return bars
    return Bars(
        time=bars.time[:-1],
        open_=bars.open_[:-1],
        high=bars.high[:-1],
        low=bars.low[:-1],
        close=bars.close[:-1],
        spread_points=bars.spread_points[:-1],
        point=bars.point,
        digits=bars.digits,
        utc_offset_s=bars.utc_offset_s,
    )


def resolve_offset_s(
    mt5: ReadOnlyMT5, settings: Settings, symbol: str, now_epoch: int | None = None
) -> int | None:
    """The broker's clock offset from UTC, or None when it cannot be established.

    Configured value first, then a live tick - the same order the diagnostic pass and the watch
    loop resolve it in, so every reader of "07:00" means the same 07:00. A tick only measures the
    offset while the market is open and moving, so None is a normal answer on a closed market,
    which is exactly when a session rule has no business guessing.
    """
    if settings.utc_offset_hours is not None:
        return int(settings.utc_offset_hours * 3600)
    try:
        tick = mt5.symbol_info_tick(symbol)
    except Exception:  # noqa: BLE001 - an unknown clock is a normal answer, not a crash
        return None
    if tick is None:
        return None
    now = int(time.time()) if now_epoch is None else int(now_epoch)
    offset = estimate_server_offset_s(int(tick.time), now)
    return offset if offset_is_plausible(offset) else None


def load_bars(
    mt5: ReadOnlyMT5,
    symbol: str,
    timeframe: str,
    count: int,
    utc_offset_s: int | None = None,
) -> Bars | None:
    """History as `Bars`. Pass `utc_offset_s` whenever the caller knows it: the session rules

    cannot place "07:00 UTC" on the broker's clock without it, and will say so rather than
    silently trading three hours early (see strategies_scalp.SessionClockError).
    """
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, min(count, SAFE_REQUEST_BARS))
    if rates is None or rates.size < 500:
        return None
    info = mt5.symbol_info(symbol)
    if info is None:
        return None
    return Bars(
        time=np.asarray(rates["time"], dtype=np.int64),
        open_=np.asarray(rates["open"], dtype="float64"),
        high=np.asarray(rates["high"], dtype="float64"),
        low=np.asarray(rates["low"], dtype="float64"),
        close=np.asarray(rates["close"], dtype="float64"),
        spread_points=np.asarray(rates["spread"], dtype=np.int32),
        point=float(info.point),
        digits=int(info.digits),
        utc_offset_s=utc_offset_s,
    )


def cost_from_spreads(spread_pips: np.ndarray, stop_pips: float) -> float:
    """All-in entry+exit cost (half-spread each side) as a fraction of the stop distance.

    The spreads come in as an argument rather than being read off `bars` here, because a
    cost-stress run hands over widened spreads: reading the bar history instead would charge the
    ordinary cost and quietly turn the stress gate into a rerun of the unstressed one.
    """
    if stop_pips <= 0:
        raise ValueError("stop_pips must be positive")
    return float(np.median(spread_pips) / stop_pips)


def cost_in_r(bars: Bars, stop_pips: float) -> float:
    """All-in entry+exit cost (half-spread each side) as a fraction of the stop distance."""
    return cost_from_spreads(bars.spread_pips(), stop_pips)


@dataclass
class Outcome:
    entry_i: int
    side: int  # +1 long, -1 short
    r: float  # net R after costs (0 = time exit)
    touched_sl: bool
    touched_tp: bool
    bars_held: int


def plan_trade(
    bars: Bars,
    sig_i: int,
    entry_bar: int,
    side: int,
    stop_pips: float,
    tp_r: float,
) -> dict[str, Any] | None:
    """Convert a closed-bar signal into an exact executable plan at the entry bar's open.

    Returns prices exactly as the live gate would compute them: longs on bid, shorts on
    ask (mid + half spread, conservative). Returns entry/sl/tp prices and cost in R
    charged over the full hold (entry+exit). Returns None if inputs would not progress.
    """
    if entry_bar - sig_i < 1 or entry_bar >= bars.n:
        return None
    pip = bars.pip()
    stop_dist = stop_pips * pip
    tp_dist = tp_r * stop_dist
    spreads = bars.spread_pips()
    if side > 0:
        entry = float(bars.open_[entry_bar])
        sl = entry - stop_dist
        tp_price = entry + tp_dist
    else:
        entry = float(bars.open_[entry_bar] + spreads[entry_bar] * pip / 2)
        sl = entry + stop_dist
        tp_price = entry - tp_dist
    return {
        "entry": entry,
        "sl": sl,
        "tp": tp_price,
        "cost_r": float(cost_in_r(bars, stop_pips) * 2),
    }


def simulate(
    bars: Bars,
    sig_i: np.ndarray,  # indices where a signal is confirmed (bar CLOSED; entry = next open)
    side: np.ndarray,  # +1/-1 per signal
    stop_pips: np.ndarray,
    tp_r: np.ndarray,  # reward-to-risk multiple
    max_hold_bars: int,
    spread_pips: np.ndarray | None = None,
) -> list[Outcome]:
    """Triple-barrier simulation. Entry at next bar open; SL-first on double touch; shorts on ask."""
    n = bars.n
    spreads = bars.spread_pips() if spread_pips is None else spread_pips
    # One position at a time (D-035): a re-fire while the rule is already in a trade is the
    # same position, not a new one, so it is not graded as another outcome here.
    taken = {k for k, _, _ in one_at_a_time(bars, sig_i, side, stop_pips, tp_r, max_hold_bars)}
    out: list[Outcome] = []
    for k in range(sig_i.size):
        if k not in taken:
            continue
        i = int(sig_i[k])
        entry_i = i + 1
        if entry_i + 1 >= n:
            continue  # no full entry bar available
        s = int(side[k])
        stop = float(stop_pips[k])
        tp = float(tp_r[k])
        pip = bars.pip()
        stop_dist = stop * pip
        tp_dist = tp * stop_dist
        if s > 0:
            entry = bars.open_[entry_i]
            sl = entry - stop_dist
            tp_price = entry + tp_dist
        else:
            # shorts fill on ask; bar mid-approx entry plus half-spread, conservatively
            entry = bars.open_[entry_i] + spreads[entry_i] * pip / 2
            sl = entry + stop_dist
            tp_price = entry - tp_dist
        cost_r = cost_from_spreads(spreads, stop)
        result: float | None = None
        touched_sl = touched_tp = False
        held = 0
        for j in range(entry_i + 1, min(n, entry_i + 1 + max_hold_bars)):
            held += 1
            if s > 0:
                sl_hit = bars.low[j] <= sl
                tp_hit = bars.high[j] >= tp_price
            else:
                sl_hit = bars.high[j] >= sl
                tp_hit = bars.low[j] <= tp_price
            if sl_hit and tp_hit:  # conservative: SL first (D-012)
                touched_sl = True
                result = -1.0
                break
            if sl_hit:
                touched_sl = True
                result = -1.0
                break
            if tp_hit:
                touched_tp = True
                result = tp
                break
        if result is None:  # time exit at close of last held bar
            px = bars.close[min(n - 1, entry_i + max_hold_bars)]
            result = s * (px - entry) / stop_dist
        net = result - cost_r * 2  # entry+exit friction charged in R
        out.append(Outcome(entry_i, s, net, touched_sl, touched_tp, held))
    return out


def resolve_outcome(
    bars: Bars,
    plan: dict[str, Any],
    sig_i: int,
    side: int,
    max_hold_bars: int,
) -> Outcome | None:
    """Grade one planned trade against history, answering 'what happened next?'"""
    if plan is None:
        return None
    entry = float(plan["entry"])
    sl = float(plan["sl"])
    tp_price = float(plan["tp"])
    stop_dist = abs(plan["sl"] - plan["entry"])
    result: float | None = None
    touched_sl = touched_tp = False
    held = 0
    # Signals on the last bar with no full entry bar forward still get graded on what
    # history actually shows, even when the trade could not have fully executed.
    for j in range(sig_i + 1, min(bars.n, sig_i + 1 + max_hold_bars)):
        held += 1
        if side > 0:
            sl_hit = bars.low[j] <= sl
            tp_hit = bars.high[j] >= tp_price
        else:
            sl_hit = bars.high[j] >= sl
            tp_hit = bars.low[j] <= tp_price
        if sl_hit and tp_hit:
            touched_sl = True
            result = -1.0
            break
        if sl_hit:
            touched_sl = True
            result = -1.0
            break
        if tp_hit:
            touched_tp = True
            result = side * (tp_price - entry) / stop_dist
            break
    if result is None:
        px = float(bars.close[min(bars.n - 1, sig_i + 1 + max_hold_bars)])
        result = side * (px - entry) / stop_dist
    net = result - float(plan["cost_r"])
    return Outcome(sig_i + 1, side, net, touched_sl, touched_tp, held)


# How a single planned trade ended. "open" is the one the grader never has to report: no
# barrier was reached and the hold window has not elapsed, so the trade is still running.
CLOSED_SL = "sl"
CLOSED_TP = "tp"
CLOSED_TIME = "time"
STILL_OPEN = "open"


def walk_trade(
    bars: Bars,
    entry_bar: int,
    side: int,
    sl: float,
    tp_price: float,
    max_hold_bars: int,
) -> tuple[int | None, str]:
    """Where one planned trade ends: (bar it closed on, how), or (None, "open") if still running.

    The barriers are tested from the bar *after* the entry bar, exactly as `simulate` grades
    them (shorts on ask, longs on bid, SL first on a double touch per D-012); a test pins the two
    together so this reading of a trade cannot drift from the graded one. The `resolve_outcome`
    grader deliberately also tests the entry bar itself so a signal with no full forward bar is
    still graded; that one bar is the only difference between the two, and it is what makes the
    functions here answer "is it still open" where the grader always answers "how did it end".
    """
    for j in range(entry_bar + 1, min(bars.n, entry_bar + 1 + max_hold_bars)):
        if side > 0:
            sl_hit, tp_hit = bars.low[j] <= sl, bars.high[j] >= tp_price
        else:
            sl_hit, tp_hit = bars.high[j] >= sl, bars.low[j] <= tp_price
        if sl_hit:  # conservative: the stop fires first when one bar touches both barriers
            return j, CLOSED_SL
        if tp_hit:
            return j, CLOSED_TP
    end = entry_bar + max_hold_bars
    if end <= bars.n - 1:
        return end, CLOSED_TIME
    return None, STILL_OPEN


def bars_held(entry_bar: int, exit_bar: int | None, n_bars: int) -> int:
    """Bars from the entry bar to now: through the exit bar when closed, to the last bar when open."""
    return n_bars - 1 - entry_bar if exit_bar is None else int(exit_bar) - entry_bar


def refires_while_open(
    sig_i: np.ndarray, k: int, entry_bar: int, live_until: int
) -> int:
    """Later signals that asked to enter again while trade `k` was already running (D-035).

    The number the live view and the chart both show as "re-signaled N×": the repeats the rule
    stood aside for, which used to be invisible because each one reset the row to a new entry.
    """
    return sum(
        1 for j in range(k + 1, int(sig_i.size)) if entry_bar < int(sig_i[j]) + 1 <= live_until
    )


def closed_r(
    kind: str,
    *,
    target_r: float,
    cost_r: float,
    entry: float,
    side: int,
    stop_dist: float,
    exit_price: float | None,
) -> float | None:
    """Net R of a taken trade: None while it is still running, else what it finished at.

    The one place the R arithmetic lives, so every reader of a trade - the live view, the export
    that draws the trades on an MT5 chart, anything later - reports the same number for the same
    trade rather than each re-deriving it slightly differently.
    """
    if kind == STILL_OPEN:
        return None
    if kind == CLOSED_TP:
        return target_r - cost_r
    if kind == CLOSED_SL:
        return -1.0 - cost_r
    if exit_price is None:
        raise ValueError("a closed-by-time trade needs its exit price")
    return ((float(exit_price) - entry) if side > 0 else (entry - float(exit_price))) / stop_dist - cost_r


def one_at_a_time(
    bars: Bars,
    sig_i: np.ndarray,
    side: np.ndarray,
    stop_pips: np.ndarray,
    tp_r: np.ndarray,
    max_hold_bars: int,
) -> list[tuple[int, int | None, str]]:
    """Positions rather than signals: the ones a rule could actually be in (D-035).

    Every rule here is stateless. While its condition holds it re-fires on each closed bar, so
    the raw signal list describes the *same position* over and over: on H1 that is 55-85% of most
    families' signals (85% for the trend-pullback rule). Grading every repeat as its own trade
    counts one position several times - it inflates n, narrows the spread in the same direction,
    and hands the live view a fresh "just entered" card every bar while the rule is one position
    deep and losing.

    This account trades one position per rule, so a signal is taken only when the previous taken
    trade has already exited.    Returns (signal index, exit bar, how it closed) per taken signal, in
    chronological order; exit bar is None for the position still running. `simulate`, the replay
    cards and the live view all read their trades through this, so they cannot disagree about
    which trades exist - and the placebo controls are filtered the same way as the rule itself.
    """
    taken: list[tuple[int, int | None, str]] = []
    busy_until = -1  # last bar the previous taken trade could still be held through
    # Decide in time order whatever order the caller hands the signals in: a rule's state is
    # chronological, and the placebo controls keep a signal's bar while randomising its side.
    order = sorted(range(int(sig_i.size)), key=lambda k: (int(sig_i[k]), k))
    for k in order:
        i = int(sig_i[k])
        entry_bar = i + 1
        if entry_bar <= busy_until or entry_bar >= bars.n:
            continue  # the previous position is still running: no room for this one
        plan = plan_trade(bars, i, entry_bar, int(side[k]), float(stop_pips[k]), float(tp_r[k]))
        if plan is None:
            continue
        exit_bar, kind = walk_trade(
            bars,
            entry_bar,
            int(side[k]),
            float(plan["sl"]),
            float(plan["tp"]),
            max_hold_bars,
        )
        taken.append((k, exit_bar, kind))
        # A position that never closed blocks every later signal: it is still open now, so
        # nothing this rule produces afterwards is a new trade.
        busy_until = bars.n - 1 if exit_bar is None else exit_bar
    return taken


def summarize(outcomes: list[Outcome]) -> dict[str, Any]:
    if not outcomes:
        return {"n": 0, "net_r_mean": None, "t_stat": None}
    r = np.array([o.r for o in outcomes])
    mean = float(r.mean())
    sd = float(r.std(ddof=1)) if r.size > 1 else 0.0
    t = mean / (sd / math.sqrt(r.size)) if sd > 0 and r.size > 1 else None
    return {
        "n": int(r.size),
        "net_r_mean": round(mean, 4),
        "net_r_sd": round(sd, 4),
        "t_stat": None if t is None else round(t, 2),
        "win_rate": round(float((r > 0).mean()), 3),
        "touched_sl_rate": round(float(sum(o.touched_sl for o in outcomes) / r.size), 3),
        "median_hold_bars": int(np.median([o.bars_held for o in outcomes])),
    }
