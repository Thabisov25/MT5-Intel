"""Tests for the watcher wiring that keeps bars/ticks fresh (master plan §12/§13).

The cycle must export real artifacts when MT5 answers, and journal a failure event
without ever crashing the cycle when the export raises.
"""

from __future__ import annotations

import json
from typing import Any

from mt5_intel import watch
from mt5_intel.store import Store
from test_market_export import FakeMT5, FakeTick


def _store(tmp_path) -> Store:
    return Store(tmp_path / "journal.sqlite")


def test_export_market_artifacts_records_written(tmp_path, monkeypatch) -> None:
    from mt5_intel import market_export

    monkeypatch.setattr(market_export, "MARKET_DIR", tmp_path / "market")
    store = _store(tmp_path)
    summary: dict[str, Any] = {}
    watch._export_market_artifacts(FakeMT5(tick=FakeTick()), ("USDJPY",), store, summary)
    assert summary["market_export"]["written"] == 4
    assert (tmp_path / "market" / "bars_USDJPY_H1.json").exists()
    assert store.recent_events("market_export_error") == []  # nothing journaled when it worked


def test_export_market_artifacts_journals_failure_not_raises(tmp_path, monkeypatch) -> None:
    from mt5_intel import market_export

    class BrokenMT5(FakeMT5):
        def copy_rates_from_pos(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("connection lost")

    monkeypatch.setattr(market_export, "MARKET_DIR", tmp_path / "market")
    store = _store(tmp_path)
    summary: dict[str, Any] = {}
    watch._export_market_artifacts(BrokenMT5(), ("USDJPY",), store, summary)
    rows = store.recent_events("market_export_error")
    assert rows[-1]["kind"] == "market_export_error"
    assert rows[-1]["payload"]["error"] == "RuntimeError"
    assert rows[-1]["payload"]["message"] == "connection lost"
    assert "market_export" not in summary
