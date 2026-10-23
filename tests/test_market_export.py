"""Tests for the terminal's real market export (master plan §12/§13).

The exporter must write only what MT5 returned — no synthesised bars, no guessed
prices — and a symbol without a tick must land as an explicit unavailable row.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from mt5_intel import market_export


class FakeMT5:
    """A read-only stand-in carrying two frames' worth of real-shaped rates and one tick."""

    def __init__(self, tick: Any = None) -> None:
        self._rates = np.array(
            [
                (1_792_749_600, 157.06, 157.127, 156.945, 156.972, 4604),
                (1_792_746_000, 157.1, 157.2, 156.9, 157.05, 5000),
            ],
            dtype=[("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8"), ("tick_volume", "<i8")],
        )
        self._tick = tick

    def copy_rates_from_pos(self, symbol: str, timeframe: str, start: int, count: int) -> np.ndarray:
        return self._rates

    def symbol_info(self, name: str) -> Any:
        return type("Info", (), {"digits": 3})()

    def symbol_info_tick(self, name: str) -> Any:
        return self._tick


class FakeTick:
    bid = 156.972
    ask = 156.994
    last = 0.0
    time = 1_792_751_538


def test_bar_rows_are_exactly_what_mt5_returned() -> None:
    mt5 = FakeMT5()
    rates = mt5.copy_rates_from_pos("USDJPY", "H1", 0, 10)
    rows = market_export._bar_rows(rates)
    assert len(rows) == 2
    assert rows[0] == {
        "time": 1_792_749_600,
        "open": 157.06,
        "high": 157.127,
        "low": 156.945,
        "close": 156.972,
        "volume": 4604,
    }


def test_export_bars_writes_readable_artifact(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(market_export, "MARKET_DIR", tmp_path)
    payload = market_export.export_bars(FakeMT5(), "USDJPY", "H1", 10)
    assert payload is not None
    written = market_export.bars_path("USDJPY", "H1")
    assert written.exists()
    assert payload["symbol"] == "USDJPY"
    assert payload["timeframe"] == "H1"
    assert payload["digits"] == 3
    assert payload["source"] == "MetaTrader5.copy_rates_from_pos"


def test_export_watch_records_real_tick_and_spread(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(market_export, "MARKET_DIR", tmp_path)
    payload = market_export.export_watch(FakeMT5(tick=FakeTick()), ("USDJPY",))
    quote = payload["quotes"]["USDJPY"]
    assert quote["available"] is True
    assert quote["bid"] == 156.972
    assert quote["ask"] == 156.994
    # (ask - bid) = 0.022 = 2.2 pips on a 3-digit quote.
    assert quote["spread_pips"] == 2.2


def test_missing_tick_is_unavailable_not_guessed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(market_export, "MARKET_DIR", tmp_path)
    payload = market_export.export_watch(FakeMT5(tick=None), ("USDJPY",))
    quote = payload["quotes"]["USDJPY"]
    assert quote == {"symbol": "USDJPY", "available": False}


def test_run_once_writes_all_frames_plus_watch(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(market_export, "MARKET_DIR", tmp_path)
    written = market_export.run_once(FakeMT5(tick=FakeTick()), ("USDJPY",), 10)
    # Three frames for one symbol, plus the watch snapshot.
    assert written == 4
    for timeframe in market_export.TIMEFRAMES:
        assert market_export.bars_path("USDJPY", timeframe).exists()
    assert market_export.watch_path().exists()
