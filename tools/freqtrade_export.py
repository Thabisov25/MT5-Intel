"""Export watched symbols' H1 bars from MT5 for freqtrade's backtest/hyperopt engine.

Freqtrade is used the only way that makes sense for an MT5 account: offline. It never touches
the broker - it crunches bars this script pulls from the terminal, exactly like the research
replays do. The split is deliberate: this file runs in the project venv (which owns the
`MetaTrader5` connection) and writes plain CSV; the conversion into freqtrade's own data
format, and every freqtrade command, run in `.venv-freqtrade`, so freqtrade's pinned
dependencies never touch the live watcher's environment.

Two details carry over from the watch loop rather than being reinvented:

* Bar stamps are the broker's clock (D-011). The offset is resolved the same way the watcher
  resolves it, and every stamp is written as UTC - freqtrade treats dates as naive UTC, so a
  stamp left three hours ahead would place the MA200 crossover and the session edges three
  hours early.
* The last bar is still forming. It is dropped (`without_forming_bar`) because every rule
  this feeds is closed-bar-only: a half-built candle would flicker in and out of the dataset
  while freqtrade reads it.

Usage (project venv):

    .venv/Scripts/python.exe tools/freqtrade_export.py            # all watched symbols, H1
    .venv/Scripts/python.exe tools/freqtrade_export.py --bars 20000 --timeframe H1
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mt5_intel.research.backtest import (  # noqa: E402 - path bootstrap above
    Bars,
    load_bars,
    resolve_offset_s,
    without_forming_bar,
)
from mt5_intel.config import load_settings, watch_symbols  # noqa: E402
from mt5_intel.mt5_adapter import ReadOnlyMT5  # noqa: E402

OUT_DIR = PROJECT_ROOT / "user_data" / "mt5_ohlcv"


def to_csv(bars: Bars, path: Path) -> int:
    """One row per closed bar, stamps on UTC. Returns the rows written."""
    utc = bars.utc_time()
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "open", "high", "low", "close", "volume", "spread_points"])
        for i in range(bars.n):
            # freqtrade's feather/json handlers want millisecond stamps; the conversion script
            # reads the seconds here so this file stays readable without pandas.
            writer.writerow(
                [
                    int(utc[i]),
                    f"{bars.open_[i]:.10g}",
                    f"{bars.high[i]:.10g}",
                    f"{bars.low[i]:.10g}",
                    f"{bars.close[i]:.10g}",
                    0,  # tick volume is not in `Bars`; spread cost is charged separately anyway
                    int(bars.spread_points[i]),
                ]
            )
    return bars.n


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=int, default=20_000, help="bars per symbol (max 80000)")
    parser.add_argument("--timeframe", default="H1", choices=["M5", "M15", "M30", "H1", "H4"])
    args = parser.parse_args()

    settings = load_settings()
    symbols = watch_symbols(settings)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    backend = __import__("MetaTrader5", fromlist=["initialize"])  # Windows-only package
    trader = ReadOnlyMT5(backend)
    trader.connect(
        login=settings.login,
        password=settings.password,
        server=settings.server,
        terminal_path=settings.terminal_path,
    )
    try:
        offset = resolve_offset_s(trader, settings, settings.symbol_root)
        if offset is None:
            print("WARNING: broker clock offset unresolved; stamps will be raw server time.")
        for symbol in symbols:
            bars = load_bars(trader, symbol, args.timeframe, args.bars, utc_offset_s=offset)
            if bars is None:
                print(f"{symbol}: not enough history, skipped")
                continue
            closed = without_forming_bar(bars)
            target = OUT_DIR / f"{symbol}_{args.timeframe}.csv"
            rows = to_csv(closed, target)
            print(f"{symbol}: {rows} closed {args.timeframe} bars -> {target}")
    finally:
        try:
            trader.shutdown()
        except Exception:  # noqa: BLE001 - a terminal that will not close is not an export fault
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
