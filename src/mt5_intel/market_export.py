"""Export real market data for the dashboard's terminal workspace (master plan §12, §13).

Additive and read-only: it connects through the same allow-listed adapter the watcher
uses (`mt5_adapter.ReadOnlyMT5`), copies recent bars and the current tick for every
watched symbol, and writes atomic JSON artifacts under `dashboard/market/`. It never
touches the watcher's own artifacts, the journal, or any position or order.

Run once:          python -m mt5_intel.market_export
Run as a loop:     python -m mt5_intel.market_export --loop --seconds 30
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import ConfigError, load_settings, watch_symbols

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MARKET_DIR = PROJECT_ROOT / "dashboard" / "market"

# The frames the dashboard's terminal offers (lib/setups.ts TIMEFRAMES), plus the
# ones the adapter's allow-list carries for completeness.
TIMEFRAMES = ("H1", "M30", "M15")

DEFAULT_BARS = 500


def bars_path(symbol: str, timeframe: str) -> Path:
    """Per symbol and frame first: one file per chart, no cross-symbol mixing."""
    return MARKET_DIR / f"bars_{symbol}_{timeframe}.json"


def watch_path() -> Path:
    return MARKET_DIR / "watch.json"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write the artifact the way the watcher writes its own (watch.py): a temp name and
    an atomic replace, so a dashboard reader that lands mid-write sees the old file whole."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _bar_rows(rates: Any) -> list[dict[str, Any]]:
    """numpy rate rows → plain OHLCV records. No synthesis: only bars MT5 returned."""
    rows: list[dict[str, Any]] = []
    for row in rates:
        rows.append(
            {
                "time": int(row["time"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["tick_volume"]) if "tick_volume" in row.dtype.names else None,
            }
        )
    return rows


def export_bars(mt5: Any, symbol: str, timeframe: str, count: int) -> dict[str, Any] | None:
    """One symbol-frame artifact from real copied rates, or None when MT5 has none."""
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    if rates is None or len(rates) == 0:
        return None
    info = mt5.symbol_info(symbol)
    digits = int(getattr(info, "digits", 0) or 0) if info is not None else None
    payload = {
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": "MetaTrader5.copy_rates_from_pos",
        "symbol": symbol,
        "timeframe": timeframe,
        "digits": digits,
        "clock": "utc",
        "bars": _bar_rows(rates),
    }
    _atomic_write_json(bars_path(symbol, timeframe), payload)
    return payload


def export_watch(mt5: Any, symbols: tuple[str, ...]) -> dict[str, Any]:
    """The Market Watch snapshot: real ticks only. A symbol without a tick gets an
    explicit unavailable entry rather than a guessed price."""
    quotes: dict[str, Any] = {}
    for symbol in symbols:
        tick = mt5.symbol_info_tick(symbol)
        info = mt5.symbol_info(symbol)
        if tick is None:
            quotes[symbol] = {"symbol": symbol, "available": False}
            continue
        bid = float(getattr(tick, "bid", 0.0) or 0.0)
        ask = float(getattr(tick, "ask", 0.0) or 0.0)
        last = float(getattr(tick, "last", 0.0) or 0.0)
        digits = int(getattr(info, "digits", 0) or 0) if info is not None else None
        spread = None
        if bid > 0 and ask > 0 and digits:
            point = 10 ** -digits
            # FX convention: one pip is ten points on 3/5-digit quotes.
            pip = point * 10
            spread = round((ask - bid) / pip, 1)
        quotes[symbol] = {
            "symbol": symbol,
            "available": True,
            "bid": bid or None,
            "ask": ask or None,
            "last": last or None,
            "tick_epoch": int(getattr(tick, "time", 0) or 0) or None,
            "digits": digits,
            "spread_pips": spread,
        }
    payload = {
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": "MetaTrader5.symbol_info_tick",
        "quotes": quotes,
    }
    _atomic_write_json(watch_path(), payload)
    return payload


def run_once(mt5: Any, symbols: tuple[str, ...], count: int) -> int:
    """One export pass. Returns the number of artifacts written."""
    written = 0
    for symbol in symbols:
        for timeframe in TIMEFRAMES:
            if export_bars(mt5, symbol, timeframe, count) is not None:
                written += 1
    export_watch(mt5, symbols)
    return written + 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export real MT5 bars and ticks for the dashboard terminal.")
    parser.add_argument("--bars", type=int, default=DEFAULT_BARS, help="bars per frame (default 500)")
    parser.add_argument("--loop", action="store_true", help="keep exporting on an interval")
    parser.add_argument("--seconds", type=int, default=30, help="loop interval in seconds")
    args = parser.parse_args(argv)

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

    from .mt5_adapter import ReadOnlyMT5

    mt5 = ReadOnlyMT5(backend)
    symbols = watch_symbols(settings)
    try:
        mt5.connect(
            login=settings.login,
            password=settings.password,
            server=settings.server,
            terminal_path=settings.terminal_path,
        )
    except ConnectionError as exc:
        print(f"MT5 connect failed: {exc}")
        return 1

    try:
        while True:
            written = run_once(mt5, symbols, args.bars)
            print(f"{datetime.now(UTC).isoformat(timespec='seconds')} wrote {written} artifacts")
            if not args.loop:
                break
            time.sleep(max(5, args.seconds))
    finally:
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
