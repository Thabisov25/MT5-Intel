"""Take profit at the previous lower-low / higher-high, measured against the fixed target.

The account holder's rule for the confluence trade: after a sell, price tends to fall only as
far as the previous lower-low and turn there, so the target should sit at that level; buys
mirror it at the previous higher-high. This study answers three questions the same history
answers for everything else the engine grades:

* does price actually turn at that previous swing (the claim itself),
* if the target were placed there - just short of it, so a fill lands before the turn -
  would the rule have earned more or fewer R than the fixed target it plans today,
* and is the answer different for sells than for buys, or for the confluence groups than
  + a choice of level definitions, because "the previous lower-low" is not one rule: the
  nearest confirmed pivot, the strictest higher-degree pivot, and the recent range low/high
  the live tableau backdrop already draws can each name a different level from the same bars.

Every convention is the engine's own: taken positions through `one_at_a_time` (D-035), the
confluence fold (two or more families agreeing on one bar and side, the group's tightest stop
planning the order), fills through `plan_trade` (next-bar-open entry, shorts on ask), SL first
on a double touch (D-012), costs in R from the measured spread. Read-only, like the other
tools: it needs the terminal running.

    ..\\.venv\\Scripts\\python.exe tools\\tp_structure_study.py            # H1
    ..\\.venv\\Scripts\\python.exe tools\\tp_structure_study.py H4
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import numpy as np

from mt5_intel.config import load_settings
from mt5_intel.mt5_adapter import ReadOnlyMT5
from mt5_intel.research.backtest import (
    Bars,
    load_bars,
    one_at_a_time,
    plan_trade,
    resolve_offset_s,
    resolve_outcome,
)
from mt5_intel.research.replay import families_for

BARS = 20_000
MAX_HOLD = 120
# A pivot is a bar whose low (or high) is the extreme of its L neighbours on *both* sides.
# L is deliberately small: a wider L finds larger swings but confirms each of them L bars
# later, and a level the rules could not have known about at entry is not a level to plan a
# target at. L=5 on H1 means the swing is known by the close of five bars after it.
PIVOT_L = 5
# The target is placed a fraction of the entry stop *short* of the level: selling into the
# prior lower-low with the target a little above it, so the fill lands while the level still
# holds rather than exactly on it (a touch of the exact low is a bounce, not a fill).
TP_BUFFER_R = 0.25
# "Turned at the swing" is generous enough to be falsifiable: the extreme must come within
# this fraction of a stop of the level, and then reverse by at least half a stop.
TOUCH_TOL_R = 0.25
BOUNCE_R = 0.5


@dataclass
class Card:
    """One taken position: where it entered, its side, its own stop and fixed target (R)."""

    i: int  # signal bar
    side: int  # +1 buy, -1 sell
    stop_pips: float
    target_r: float
    family: str


def first_confirmed_pivot(bars: Bars, i: int, side: int, pivot_l: int = PIVOT_L) -> float | None:
    """The price of the most recent confirmed swing low (sells) or high (buys), seen by bar i.

    Confirmed means the pivot bar p is the strict extreme of p-L .. p+L and has all its
    confirmation bars closed by bar i (p + L <= i), so the level was knowable at the signal
    bar's close. Closest to the present first; None when no usable pivot is in history.
    """
    series = bars.low if side < 0 else bars.high
    for p in range(min(bars.n - pivot_l - 1, i) - pivot_l, pivot_l - 1, -1):
        if p + pivot_l > i:
            continue
        window = slice(p - pivot_l, p + pivot_l + 1)
        seg = float(series[window].min()) if side < 0 else float(series[window].max())
        if seg == float(series[p]):
            return seg
    return None


LEVEL_MODES = (
    ("pivot L5", {"pivot": 5}),
    ("pivot L12", {"pivot": 12}),
    ("range 96", {"range_bars": 96}),
    ("range 240", {"range_bars": 240}),
)


def level_of(bars: Bars, i: int, side: int, *, pivot: int = PIVOT_L, range_bars: int = 0) -> float | None:
    """One level definition at bar i. `pivot` names a pivot-strictness; `range_bars` a lookback."""
    if range_bars:
        lo = max(0, i - range_bars + 1)
        return float(bars.low[lo : i + 1].min()) if side < 0 else float(bars.high[lo : i + 1].max())
    return first_confirmed_pivot(bars, i, side, pivot_l=pivot)


def structure_r(
    bars: Bars,
    i: int,
    side: int,
    entry: float,
    stop_dist: float,
    *,
    pivot: int = PIVOT_L,
    range_bars: int = 0,
) -> float | None:
    """The R multiple a structure target sits at, or None when the level cannot name one."""
    level = level_of(bars, i, side, pivot=pivot, range_bars=range_bars)
    if level is None:
        return None
    if side < 0:  # sell: the target sits just above the supportive low
        target = level + TP_BUFFER_R * stop_dist
        if target >= entry:
            return None
        return (entry - target) / stop_dist
    target = level - TP_BUFFER_R * stop_dist
    if target <= entry:
        return None
    return (target - entry) / stop_dist


def reversal_at_swing(
    bars: Bars, entry_bar: int, side: int, stop_dist: float
) -> bool | None:
    """Whether the trade's extreme came at (or near) the previous swing, and turned there."""
    i = entry_bar - 1
    level = first_confirmed_pivot(bars, i, side)
    if level is None:
        return None
    end = min(bars.n, entry_bar + 1 + MAX_HOLD)
    if end <= entry_bar + 1:
        return None
    forward = slice(entry_bar + 1, end)
    if side < 0:
        went = float(bars.low[forward].min())
        if not (abs(went - level) <= TOUCH_TOL_R * stop_dist):
            return False
        turned = float(bars.low[forward].max()) - went >= BOUNCE_R * stop_dist
    else:
        went = float(bars.high[forward].max())
        if not (abs(went - level) <= TOUCH_TOL_R * stop_dist):
            return False
        turned = went - float(bars.high[forward].min()) >= BOUNCE_R * stop_dist
    return bool(turned)


def collect(bars: Bars) -> list[Card]:
    """Every taken position over the window, plus which families agree on each bar and side."""
    cards: list[Card] = []
    for family in families_for("H1"):
        try:
            sig_i, side, stops, tp_r = (np.asarray(a) for a in family["fn"](bars))
        except Exception as exc:  # noqa: BLE001 - one broken rule must not cost the rest
            print(f"  {family['id']}: skipped ({type(exc).__name__})")
            continue
        if sig_i.size == 0:
            continue
        for k, _exit, _kind in one_at_a_time(bars, sig_i, side, stops, tp_r, MAX_HOLD):
            if int(side[k]) not in (1, -1) or float(stops[k]) <= 0 or float(tp_r[k]) <= 0:
                continue
            cards.append(Card(int(sig_i[k]), int(side[k]), float(stops[k]), float(tp_r[k]), family["id"]))
    cards.sort(key=lambda c: (c.i, c.side, c.family))
    return cards


def group_by_families(bars: Bars) -> dict[tuple[int, int], int]:
    """For the confluence fold: per (bar, side), how many *distinct families* agree there.

    A real agreement is two or more distinct rules on one bar and side - exactly what
    `execution.plans` folds - so counting an agreeing signal that is a family's own re-fire
    as two members would inflate the composite's sample.
    """
    per_key: dict[tuple[int, int], set[str]] = {}
    for card in collect(bars):
        per_key.setdefault((card.i, card.side), set()).add(card.family)
    return {key: len(names) for key, names in per_key.items() if len(names) >= 2}


def _fmt(rows: list[float]) -> str:
    if not rows:
        return f"{'--':>5} {'--':>8} {'--':>5}"
    arr = np.asarray(rows)
    return f"{len(rows):>5} {arr.mean():>+8.3f} {(100 * (arr > 0).mean()):>5.0f}"


def run(bars: Bars) -> int:
    print(
        f"pivot L={PIVOT_L}, target buffer {TP_BUFFER_R:g}R short of the level, hold {MAX_HOLD} bars"
    )
    cards = collect(bars)
    groups = group_by_families(bars)
    print(f"taken cards {len(cards)}; agreeing confluence groups {len(groups)}")

    header = f"\n{'mode':14s} {'bucket':14s} {'fixed target':>25}   {'structure target':>25}"
    print(header)
    for mode_name, kwargs in LEVEL_MODES:
        stats: dict[str, dict[str, list[float]]] = {}
        for card in cards:
            plan = plan_trade(bars, card.i, card.i + 1, card.side, card.stop_pips, card.target_r)
            fixed = resolve_outcome(bars, plan, card.i, card.side, MAX_HOLD) if plan else None
            if plan is None:
                continue
            entry = float(plan["entry"])
            stop_dist = abs(plan["sl"] - entry)
            sr = structure_r(bars, card.i, card.side, entry, stop_dist, **kwargs)
            if sr is None:
                continue
            splan = plan_trade(bars, card.i, card.i + 1, card.side, card.stop_pips, max(sr, 0.05))
            sfixed = resolve_outcome(bars, splan, card.i, card.side, MAX_HOLD) if splan else None
            is_confluence = groups.get((card.i, card.side), 0) >= 2
            label = "confluence" if is_confluence else "single"
            for bucket in ("all", label, "sell" if card.side < 0 else "buy"):
                if fixed is not None:
                    stats.setdefault(bucket, {"fixed": [], "struct": []})["fixed"].append(fixed.r)
                if sfixed is not None:
                    stats.setdefault(bucket, {"fixed": [], "struct": []})["struct"].append(sfixed.r)
        for bucket in ("all", "confluence", "single", "sell", "buy"):
            if bucket not in stats:
                continue
            data = stats[bucket]
            print(f"{mode_name:14s} {bucket:14s} {_fmt(data['fixed'])}   {_fmt(data['struct'])}")
        print()

    # The claim itself: how often a trade's extreme came at the previous swing and turned there.
    for side_label, sd in (("sell", -1), ("buy", +1)):
        at = not_at = 0
        for card in cards:
            if card.side != sd:
                continue
            plan = plan_trade(bars, card.i, card.i + 1, sd, card.stop_pips, card.target_r)
            if plan is None:
                continue
            res = reversal_at_swing(bars, card.i + 1, sd, abs(plan["sl"] - plan["entry"]))
            if res:
                at += 1
            elif res is False:
                not_at += 1
        total = at + not_at
        share = f"{100 * at / total:.0f}%" if total else "--"
        print(f"turns at the previous swing, {side_label}s: {at}/{total} = {share}")
    return 0


def main() -> int:
    timeframe = sys.argv[1] if len(sys.argv) > 1 else "H1"
    settings = load_settings()
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
        symbol = settings.symbol_root
        bars = load_bars(mt5, symbol, timeframe, BARS, utc_offset_s=resolve_offset_s(mt5, settings, symbol))
        if bars is None:
            print("Insufficient history from terminal.")
            return 2
    finally:
        mt5.shutdown()
    return run(bars)


if __name__ == "__main__":
    raise SystemExit(main())
