"""The fade rule against the agreeing-pair rule, graded the way the watcher actually trades.

The user's rule: when two or more strategies agree on one candle, the confluence entry's side is
the market's placement, not the members' agreement - top third of the 96-bar range sells, bottom
third buys, and the middle stands down (nothing was decided there). The current rule takes the
members' own side with the tightest member's stop.

Grading follows the live watcher, not the research backtester's fixed barriers. The watcher
manages every supervised position (`_manage_stops`): at `AUTO_BREAKEVEN_R` of profit the stop
locks a fraction (`AUTO_LOCK_R`) beyond entry, at `AUTO_TRAIL_R` it trails
`AUTO_TRAIL_DISTANCE_R` behind the market, and it only ever moves in the profitable direction.
That management replayed bar by bar can turn a trade whose fixed stop would have lost into a
locked win - which is exactly the reading the user asked this study to use ("don't look at
whether it reached TP; look at whether the stop had moved, and would that moved stop be a
profit or a loss"). Both grading modes run on both rules over the same agreeing groups, so the
rows differ only in what they are meant to differ in.

The emulation is deliberately one beat *behind* the watcher: management reads each bar's close,
so a moved stop only protects the bars after the bar that moved it. A 30-second watcher would
often move the stop intrabar; grading it on the close is the conservative reading, and it is
applied to both rules identically. SL first on a double touch (D-012) is kept, and a stop that
would cross the target is left alone, exactly as `_manage_stops` does.

Every other convention is the engine's own: positions one per rule (`one_at_a_time`), fills
(`plan_trade` - next-bar-open entry, shorts on ask), costs the measured spread, and the range
read (`setups.placement_at` - the previous-96-bar window including the signal bar, fresh flags
against the 96 bars before). The agreeing groups are taken live cards agreeing on the signal
bar and side, exactly what `execution.plans` folds.

It needs the live terminal the way `combo_table.py` does. Read-only.

    .\\.venv\\Scripts\\python.exe tools\\confluence_fade_replay.py            # H1
    .\\.venv\\Scripts\\python.exe tools\\confluence_fade_replay.py M15
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import numpy as np

from mt5_intel.config import Settings, load_settings
from mt5_intel.mt5_adapter import ReadOnlyMT5
from mt5_intel.research.backtest import (
    Bars,
    Outcome,
    load_bars,
    one_at_a_time,
    plan_trade,
    resolve_offset_s,
    resolve_outcome,
)
from mt5_intel.research.replay import families_for
from mt5_intel.research.setups import CONTEXT_RANGE_BARS, placement_at

RANGE_BARS = CONTEXT_RANGE_BARS
BARS = 20_000
MAX_HOLD = 120


@dataclass
class Managed:
    """One trade graded with the watcher's stop-management rules replayed at each close."""

    r: float
    kind: str  # "tp", "stop" (the original stop), "trail" (a stop management had moved), "time"


@dataclass
class Fixed:
    """One trade graded the research way: barriers in place, untouched until one is hit."""

    r: float


def cards_of(bars: Bars, timeframe: str) -> list[tuple[int, int, int, float, float, str]]:
    """Every live card in the replay's window: (signal bar, side, stop pips, tp_r, family)."""
    cards: list[tuple[int, int, int, float, float, str]] = []
    for family in families_for(timeframe):
        try:
            sig_i, side, stops, tp_r = (np.asarray(a) for a in family["fn"](bars))
        except Exception as exc:  # noqa: BLE001 - one broken rule must not cost the rest
            print(f"  {family['id']}: skipped ({type(exc).__name__})")
            continue
        if sig_i.size == 0:
            continue
        for k, _exit, _kind in one_at_a_time(bars, sig_i, side, stops, tp_r, MAX_HOLD):
            i, sd = int(sig_i[k]), int(side[k])
            stop_pips, target_r = float(stops[k]), float(tp_r[k])
            if sd not in (1, -1) or stop_pips <= 0 or target_r <= 0 or i + 1 >= bars.n:
                continue
            cards.append((i, sd, stop_pips, target_r, family["id"]))
    cards.sort(key=lambda c: (c[0], -c[2], c[4]))
    return cards


def managed_grade(
    bars: Bars,
    sig_i: int,
    sd: int,
    stop_pips: float,
    target_r: float,
    cfg: Settings,
) -> Managed | None:
    """One planned confluence trade, held and managed the way the watcher holds its own.

    Barriers of bar j are tested against the stop and target as they stand at j's start; the
    management pass runs on j's close and protects later bars only. The two move rules and
    their guards are `_manage_stops`'s own: lock at `AUTO_BREAKEVEN_R`, trail at `AUTO_TRAIL_R`,
    improvement-only, never past the target.
    """
    plan = plan_trade(bars, sig_i, sig_i + 1, sd, stop_pips, target_r)
    if plan is None:
        return None
    entry, cost_r = float(plan["entry"]), float(plan["cost_r"])
    stop_dist = abs(plan["sl"] - entry)
    tp_current = float(plan["tp"])
    sl_current = float(plan["sl"])

    for j in range(sig_i + 2, min(bars.n, sig_i + 1 + MAX_HOLD + 1)):
        if sd > 0:
            sl_hit, tp_hit = bars.low[j] <= sl_current, bars.high[j] >= tp_current
        else:
            sl_hit, tp_hit = bars.high[j] >= sl_current, bars.low[j] <= tp_current
        if sd * (sl_current - entry) / stop_dist >= 0:  # a managed stop: win-side or locked, SL first
            if sl_hit:
                return Managed(sd * (sl_current - entry) / stop_dist - cost_r, "trail")
            if tp_hit:
                return Managed(target_r - cost_r, "tp")
        else:  # the original stop, on the losing side of the trade
            if sl_hit:
                return Managed(-1.0 - cost_r, "stop")
            if tp_hit:
                return Managed(target_r - cost_r, "tp")
        mark = float(bars.close[j])
        profit_r = sd * (mark - entry) / stop_dist
        options: list[float] = []
        if profit_r >= cfg.auto_breakeven_r:
            options.append(
                entry + cfg.auto_lock_r * stop_dist if sd > 0 else entry - cfg.auto_lock_r * stop_dist
            )
        if profit_r >= cfg.auto_trail_r:
            options.append(
                mark - cfg.auto_trail_distance_r * stop_dist if sd > 0 else mark + cfg.auto_trail_distance_r * stop_dist
            )
        if not options:
            continue
        new_sl = max(options) if sd > 0 else min(options)
        point = bars.point
        improves = (sd > 0 and new_sl > sl_current + point) or (sd < 0 and new_sl < sl_current - point)
        if not improves:
            continue
        move_r = sd * (new_sl - entry) / stop_dist
        if move_r >= target_r:  # the stop would sit on or past the target: skip, as _manage_stops does
            continue
        sl_current = new_sl

    end = sig_i + 1 + MAX_HOLD
    px = float(bars.close[min(end, bars.n - 1)])
    return Managed(sd * (px - entry) / stop_dist - cost_r, "time")


def fixed_grade(bars: Bars, sig_i: int, sd: int, stop_pips: float, target_r: float) -> Fixed | None:
    """The research backtester's answer: fixed stop, fixed target, no management."""
    plan = plan_trade(bars, sig_i, sig_i + 1, sd, stop_pips, target_r)
    if plan is None:
        return None
    outcome: Outcome | None = resolve_outcome(bars, plan, sig_i, sd, MAX_HOLD)
    return None if outcome is None else Fixed(outcome.r)


def fade_of(range_pos: float | None) -> str | None:
    """The user's placement rule: high sells, low buys, the middle stands down."""
    if range_pos is None:
        return None
    if range_pos >= 2 / 3:
        return "sell"
    if range_pos <= 1 / 3:
        return "buy"
    return None


def _fmt(mean: float | None) -> str:
    return "  n/a" if mean is None else f"{mean:+.3f}"


def run(bars: Bars, cfg: Settings) -> int:
    cards = cards_of(bars, "H1")
    groups: dict[tuple[int, int], list[tuple[int, int, int, float, float, str]]] = {}
    for card in cards:
        groups.setdefault((card[0], card[1]), []).append(card)
    agreed = {k: v for k, v in groups.items() if len(v) >= 2}
    print(f"taken cards {len(cards)}; agreeing groups (>=2 rules, one bar, one side) {len(agreed)}")

    stats: dict[str, dict[str, list]] = {}

    def record(label: str, r: float, sd: int, kind: str) -> None:
        bucket = stats.setdefault(label, {"r": [], "wins": 0, "sides": [], "kinds": {}})
        bucket["r"].append(r)
        bucket["wins"] += int(r > 0)
        bucket["sides"].append(sd)
        bucket["kinds"][kind] = bucket["kinds"].get(kind, 0) + 1

    skipped = 0
    for (i, members_side) in sorted(agreed):
        # The engine's fold: the tightest stop of the agreeing group plans the trade.
        group = agreed[(i, members_side)]
        tightest = min(group, key=lambda c: c[2])
        stop_pips, target_r = tightest[2], tightest[3]
        pos, _fresh_high, _fresh_low = placement_at(bars, i)
        fade = fade_of(pos)
        if members_g := managed_grade(bars, i, members_side, stop_pips, target_r, cfg):
            record("members' side total, managed", members_g.r, members_side, members_g.kind)
        if members_f := fixed_grade(bars, i, members_side, stop_pips, target_r):
            record("members' side total, fixed barriers", members_f.r, members_side, "fixed")
        if fade:
            fade_side = 1 if fade == "buy" else -1
            if fade_g := managed_grade(bars, i, fade_side, stop_pips, target_r, cfg):
                record(f"fade ({fade} at the {'top' if fade == 'sell' else 'bottom'} third), managed", fade_g.r, fade_side, fade_g.kind)
            if fade_f := fixed_grade(bars, i, fade_side, stop_pips, target_r):
                record(f"fade ({fade}), fixed barriers", fade_f.r, fade_side, "fixed")
        else:
            skipped += 1
    if skipped:
        print(f"  {skipped} agreeing groups stood down (middle third: the fade rule has no side there)")

    print(f"\n{'rule':56s} {'n':>5} {'meanR':>8} {'wins':>5} {'win%':>5}  exits")
    for label, data in sorted(stats.items()):
        rows = data["r"]
        mean = float(np.mean(rows)) if rows else None
        exits = ", ".join(f"{kind} {count}" for kind, count in sorted(data["kinds"].items()))
        print(
            f"{label:56s} {len(rows):>5} {_fmt(mean):>8} {data['wins']:>5} "
            f"{(100 * data['wins'] / len(rows)):>5.0f}  {exits}"
        )
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

    cfg = settings
    print(f"{symbol} {timeframe}: watcher management ON (breakeven at {cfg.auto_breakeven_r:g}R locking "
          f"{cfg.auto_lock_r:g}R, trail from {cfg.auto_trail_r:g}R at {cfg.auto_trail_distance_r:g}R)")
    return run(bars, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
