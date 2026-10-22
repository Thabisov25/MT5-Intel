"""Every candle two rules pointed at, named the way the console names it, with what it paid.

The console does not trade a two-arrow candle and does not guess a side for it: it names the exact
combination and reports what the history measured. This produces both halves of that report - the
per-combination record transcribed into `dashboard/lib/combos.ts` as `COMBINATION_RECORD`, and the
evidence lines behind `COMBINATION_STUDY` - so either can be rebuilt from the export when the history
grows, instead of being re-derived by hand.

The bars are loaded through the same call the export uses, and a row's `entry_time` is matched to its
bar by formatting that bar's own clock reading, which is how the exporter wrote it: no offset is
guessed here, and a DST change cannot silently shift a placement by an hour.

Not a test in the suite: it needs the live terminal, the way `check_chart_export.py` does. Read-only.

    .\\.venv\\Scripts\\python.exe tools\\combo_table.py            # H1
    .\\.venv\\Scripts\\python.exe tools\\combo_table.py M15
"""

from __future__ import annotations

import collections
import csv
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from mt5_intel.config import load_settings
from mt5_intel.mt5_adapter import ReadOnlyMT5
from mt5_intel.research import export_mt5
from mt5_intel.research.backtest import load_bars, resolve_offset_s

# One letter per rule, the same table `dashboard/lib/combos.ts` carries. Duplicated on purpose: the
# names have to agree with the console, and a name that silently differs would make this table
# describe candles the page cannot find.
CODES = {
    "asian_fade": "A",
    "carry_trend": "B",
    "compression_breakout": "C",
    "daily_momentum": "D",
    "ma_crossover": "E",
    "orb_london": "F",
    "pullback_scalp": "G",
    "range_extreme_mr": "H",
    "rsi_mean_reversion": "I",
    "sr_flip": "J",
    "sweep_reclaim": "K",
    "trend_pullback": "L",
}
# The two kinds of member, for the question "was it the rule that follows through or the one that
# waits for a pullback that paid". Held as sets of ids rather than read from the catalogue: this is
# the reading being tested, and it has to be stated explicitly to be checkable.
FOLLOWS_THROUGH = {
    "compression_breakout",
    "sweep_reclaim",
    "sr_flip",
    "ma_crossover",
    "daily_momentum",
    "carry_trend",
    "orb_london",
}
# The window the console's context measures placement against, so a read here and a read on the page
# are the same measurement.
RANGE_BARS = 96
BARS = 20_000


def code(family: str, side: str) -> str:
    return f"{CODES.get(family, '?')}{'↑' if side == 'buy' else '↓'}"


def read_rows(path: Path, attempts: int = 6) -> list[dict[str, str]]:
    """The export, retried briefly.

    The chart indicator reads this exact file on its own timer, and on Windows a reader that arrives
    mid-read is refused the handle rather than waiting. That is a scheduling accident, not a missing
    file, so it is worth half a second of patience before failing.
    """
    for attempt in range(attempts):
        try:
            with path.open(encoding="utf-8", newline="") as handle:
                return list(csv.DictReader(handle))
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.5)
    return []


def main() -> int:
    timeframe = sys.argv[1] if len(sys.argv) > 1 else "H1"
    settings = load_settings()
    import MetaTrader5 as backend

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
        assert bars is not None
        data_path = mt5.terminal_data_path()
        if data_path is None:  # a terminal that is not running publishes no folder to read from
            print("the terminal has not published its data folder; is it running?", file=sys.stderr)
            return 1
        path = Path(data_path) / "MQL5" / "Files" / export_mt5.filename(timeframe)
        rows = read_rows(path)
    finally:
        mt5.shutdown()

    # The exporter stamps a row with its bar's own clock reading, so the label is the key back to the
    # bar - the same reading, on the same bars, with nothing inferred in between.
    index_of = {
        datetime.fromtimestamp(int(bars.time[i]), UTC).strftime("%Y.%m.%d %H:%M"): i
        for i in range(bars.n)
    }
    by_bar: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
    for row in rows:
        by_bar[row["entry_time"]].append(row)
    groups = {bar: group for bar, group in by_bar.items() if len({r["side"] for r in group}) > 1}

    seen: collections.Counter[str] = collections.Counter()
    paid: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    thirds: collections.Counter[tuple[str, str]] = collections.Counter()
    extremes: collections.Counter[tuple[str, str]] = collections.Counter()
    kinds: collections.Counter[str] = collections.Counter()
    placed = 0
    for bar, group in groups.items():
        key = " ".join(sorted(code(r["family"], r["side"]) for r in group))
        seen[key] += 1
        finished = [r for r in group if r["result_r"] not in ("", "None")]
        if len(finished) < 2:
            continue  # one side has not finished, so "which side paid" is not a question yet
        winner = max(finished, key=lambda r: float(r["result_r"]))
        paid[key][winner["side"]] += 1

        i = index_of.get(bar)
        if i is None or i < RANGE_BARS:  # a bar outside the loaded history cannot be placed
            continue
        placed += 1
        high = float(bars.high[i - RANGE_BARS : i].max())
        low = float(bars.low[i - RANGE_BARS : i].min())
        close, bar_high, bar_low = float(bars.close[i]), float(bars.high[i]), float(bars.low[i])
        if high > low:
            fraction = (close - low) / (high - low)
            thirds[
                ("bottom third" if fraction <= 1 / 3 else "top third" if fraction >= 2 / 3 else "middle", winner["side"])
            ] += 1
        if bar_low <= low:
            extremes[("fresh low", winner["side"])] += 1
        elif bar_high >= high:
            extremes[("fresh high", winner["side"])] += 1
        if {r["family"] in FOLLOWS_THROUGH for r in group} == {True, False}:
            kinds["follows through" if winner["family"] in FOLLOWS_THROUGH else "waits for a pullback"] += 1

    print(f"// {path.name}: {len(groups)} combination candles, {len(seen)} distinct combinations")
    print("export const COMBINATION_RECORD: Record<string, CombinationRecord> = {")
    for key in sorted(seen, key=lambda k: (-seen[k], k)):
        record = paid.get(key)
        buys = "null" if record is None else record["buy"]
        sells = "null" if record is None else record["sell"]
        print(f'  "{key}": {{ seen: {seen[key]}, buyPaid: {buys}, sellPaid: {sells} }},')
    print("};")

    resolved = sum(paid[key].total() for key in paid)
    buys = sum(record["buy"] for record in paid.values())
    print(f"\nresolved: {resolved}  the buy side paid {buys}, the sell side {resolved - buys}")
    # A call is a candle the rule has an opinion about: the bottom and top thirds, or a fresh extreme:
    # the middle band is "no side", and counting it would flatter or damn the rule for nothing.
    calls = thirds[("bottom third", "buy")] + thirds[("bottom third", "sell")]
    calls += thirds[("top third", "buy")] + thirds[("top third", "sell")]
    right = thirds[("bottom third", "buy")] + thirds[("top third", "sell")]
    extreme_calls = extremes[("fresh low", "buy")] + extremes[("fresh low", "sell")]
    extreme_calls += extremes[("fresh high", "buy")] + extremes[("fresh high", "sell")]
    extreme_right = extremes[("fresh low", "buy")] + extremes[("fresh high", "sell")]
    print(f"placement read: the buy-at-a-low / sell-at-a-high rule called {calls} candles")
    print(f"  and the side it named matched the side that paid on {right} of them")
    print(f"  at fresh {RANGE_BARS}-bar extremes it called {extreme_calls} and was right on {extreme_right}")
    print(f"  the middle third, where the rule says nothing, held {thirds[('middle', 'buy')] + thirds[('middle', 'sell')]} candles")
    print(f"member kinds, on the candles holding one of each: {dict(kinds)}")
    print("\nplacement here is the same read the console shows: the arrow bar's close against the")
    print(f"previous {RANGE_BARS} bars, fresh highs and lows against the same window. Bars placed: {placed}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
