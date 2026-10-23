"""Convert the MT5 bar export into freqtrade's data files.

Run in the freqtrade venv, not the project one:

    .venv-freqtrade/Scripts/python.exe tools/freqtrade_sync.py

The conversion goes through freqtrade's own data handler rather than hand-writing feather
files, so the on-disk format is whatever the installed freqtrade version expects - the format
is an implementation detail of that library, not something this repo should pin by hand.

Pairs are named the way spot markets name them ("USD/JPY"); the filename map is explicit
because MT5 symbols are concatenated and a wrong split ("USDJ/PY") would silently produce an
empty backtest rather than an error.

Both data sets are written: spot, and the futures trio (candles, mark, zeroed funding). The
backtest runs in futures mode because both families signal both directions and spot markets
cannot short; the flat funding stream is the honest absence of a cost, not a hidden one - an
FX account pays swap, which MT5 books outside the candle prices.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CSV_DIR = PROJECT_ROOT / "user_data" / "mt5_ohlcv"
DATA_DIR = PROJECT_ROOT / "user_data" / "data" / "kraken"
TIMEFRAME = "1h"

# MT5 symbol -> market pair. Explicit pairs only: an unknown symbol is an error, not a guess.
PAIRS = {
    "USDJPY": "USD/JPY",
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
    "AUDUSD": "AUD/USD",
    "NZDUSD": "NZD/USD",
    "USDCAD": "USD/CAD",
    "USDCHF": "USD/CHF",
    "EURJPY": "EUR/JPY",
    "GBPJPY": "GBP/JPY",
    "XAUUSD": "XAU/USD",
}


def load_csv(path: Path) -> pd.DataFrame:
    """The frame freqtrade's handler stores: date in ms, OHLCV floats.

    Volume is a constant 1: tick volume did not travel through `Bars`, and every candle in a
    closed-bar export traded - the spread cost is charged by the research rules in R, not by
    freqtrade's volume filters.
    """
    dates: list[int] = []
    rows: list[list[float]] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            dates.append(int(row["date"]) * 1000)  # seconds -> the milliseconds the handlers want
            rows.append([float(row[c]) for c in ("open", "high", "low", "close")] + [1.0])
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], dtype="float64")
    frame.insert(0, "date", pd.to_datetime(pd.Series(dates, dtype="int64"), unit="ms", utc=True))
    return frame


def main() -> int:
    from freqtrade.data.history import get_datahandler  # freqtrade venv only
    from freqtrade.enums import CandleType

    # The exact writer API is a detail of the installed freqtrade version (this one wants
    # a DataFrame plus an explicit candle type), which is exactly why the conversion runs
    # through the library instead of this repo hand-writing the on-disk format.
    handler = get_datahandler(DATA_DIR, "feather")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / TIMEFRAME).mkdir(parents=True, exist_ok=True)

    written = 0
    for csv_path in sorted(CSV_DIR.glob("*_H1.csv")):
        symbol = csv_path.stem.removesuffix("_H1")
        pair = PAIRS.get(symbol)
        if pair is None:
            print(f"{symbol}: no pair mapping, skipped")
            continue
        data = load_csv(csv_path)
        handler.ohlcv_store(pair, TIMEFRAME, data, CandleType.SPOT)
        # Futures mode, because both families signal both directions and spot cannot short.
        # The futures set is the same candles under the settled-pair name, plus mark candles
        # (the trades themselves - an approximation, and an honest one for a pilot) and a flat
        # zero funding stream: funding is a crypto cost model, and charging the FX carry trade
        # a synthetic funding bleed would invent a cost the MT5 account never pays.
        # Settled in USDT because freqtrade validates pair *naming* against the exchange even
        # offline, and the binance namespace it accepts for futures names its settle currency
        # USDT. The unit is bookkeeping only: every number this engine reports is a ratio.
        fut_pair = f"{pair}:USDT"
        handler.ohlcv_store(fut_pair, TIMEFRAME, data, CandleType.FUTURES)
        handler.ohlcv_store(fut_pair, TIMEFRAME, data, CandleType.MARK)
        funding = pd.DataFrame(
            {"date": data["date"], "funding_rate": 0.0},
        )
        handler.ohlcv_store(fut_pair, TIMEFRAME, funding, CandleType.FUNDING_RATE)
        written += 1
        print(f"{pair}: {len(data)} candles -> {DATA_DIR} (spot + futures/mark/funding)")
    if not written:
        print("Nothing converted - run tools/freqtrade_export.py in the project venv first.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
