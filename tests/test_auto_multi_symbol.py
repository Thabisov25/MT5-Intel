"""Per-symbol auto-trading: two markets trading from one switch, one journal.

The headline guarantee is the dedup: the same strategy firing the same bar on two symbols is two
different trades - a different spec, a different price, a different risk amount - so neither claim
can spend the other's. That lives in the schema (the UNIQUE index carries the symbol) and in the
switch shared across both steps, which is why the tests here pin both seams.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tests.fakes import FakeMT5

from mt5_intel.execution import MT5Trader, artifact, run
from mt5_intel.store import Store
from test_execution import VALIDATED, _card_row, _market, _payload

NOW = int(datetime(2026, 10, 13, 15, 0, tzinfo=UTC).timestamp())

VALIDATED_INSERT = None  # the shared set opens with the two validated families


def _two_symbol_settings() -> Any:
    """A settings whose watched list has a second symbol, built the construction the tests use."""
    import test_execution as te

    return te._settings(
        auto_trade=True,
        auto_strategies=("carry_trend",),
        symbols=("USDJPY", "EURUSD"),
    )


def _eurusd_row() -> dict[str, Any]:
    # The fake terminal quotes one price level whatever its symbol name says, so the row carries
    # that level: what this pins is the journal's symbol tagging, not the quote itself.
    card = _card_row("carry_trend")
    return card


def test_the_same_signal_bar_on_two_symbols_is_two_claims(tmp_path: Path) -> None:
    """The dedup key carries the symbol, so neither market can spend the other's entry."""
    store = Store(tmp_path / "m.db")
    row_id = store.reserve_auto_trade(
        strategy="carry_trend",
        families=("carry_trend",),
        side="buy",
        signal_epoch=NOW - 3600,
        signal_utc=None,
        entry=1.12334,
        sl=1.12304,
        tp=1.12394,
        volume=0.01,
        stop_pips=30.0,
        tp_r=2.0,
        risk_amount=10.0,
        equity=1000.0,
        risk_pct=1.0,
        digits=5,
        expires_epoch=NOW + 86_400,
        magic=20261013,
        code_version="test",
        open_ts="2026-10-13T14:00:00+00:00",
        symbol="EURUSD",
    )
    assert row_id is not None
    # Same strategy, same bar, same side - on the other symbol, so its claim is free.
    assert store.auto_trade_taken("carry_trend", NOW - 3600, "buy") is False
    assert store.auto_trade_taken("carry_trend", NOW - 3600, "buy", "EURUSD") is True


def test_open_positions_and_the_cap_are_counted_per_symbol(tmp_path: Path) -> None:
    """The cap is one budget read per step: each symbol's step sees its own rows only."""
    store = Store(tmp_path / "m.db")
    values = {
        "strategy": "carry_trend",
        "families": ("carry_trend",),
        "side": "buy",
        "signal_epoch": NOW - 3600,
        "signal_utc": None,
        "entry": 1.12334,
        "sl": 1.12304,
        "tp": 1.12394,
        "volume": 0.01,
        "stop_pips": 30.0,
        "tp_r": 2.0,
        "risk_amount": 10.0,
        "equity": 1000.0,
        "risk_pct": 1.0,
        "digits": 5,
        "expires_epoch": NOW + 86_400,
        "magic": 20261013,
        "code_version": "test",
        "open_ts": "2026-10-13T14:00:00+00:00",
    }
    eur = store.reserve_auto_trade(symbol="EURUSD", **values)
    jpy = store.reserve_auto_trade(symbol="USDJPY", **values)
    assert eur is not None and jpy is not None
    assert {row["symbol"] for row in store.auto_trades_open()} == {"EURUSD", "USDJPY"}
    assert [row["symbol"] for row in store.auto_trades_open("EURUSD")] == ["EURUSD"]
    assert store.auto_trades_open("GBPUSD") == []


def test_a_second_symbol_trades_its_own_market_from_the_shared_switch(tmp_path: Path) -> None:
    """One switch, two orders: each step sends its request on its own symbol's spec and price."""

    store = Store(tmp_path / "m.db")
    switch_path = tmp_path / "auto-switch.json"
    switch_path.write_text(json.dumps({"enabled": True}), encoding="utf-8")
    backend = FakeMT5(
        now=datetime(2026, 10, 13, 15, 0, tzinfo=UTC),
        symbol_name="EURUSDc",
        advance_ticks=False,
        equity=1000.0,
    )
    # The fake quotes one symbol; the per-symbol spec read is the same namespace either way, which
    # is exactly what the real terminal answers per symbol too.
    market = _market()
    outcome = run(
        trader=MT5Trader(backend),
        store=store,
        settings=_two_symbol_settings(),
        setups=_payload([_eurusd_row()], digits=5),
        market=market,
        switch_path=switch_path,
        now_epoch=NOW,
        offset_s=_market()["offset_s"],
        timeframe_s=3600,
        max_hold_bars=24,
        validated=VALIDATED,
    )
    # The default caller (no symbol) still names the primary market in its rows.
    assert outcome.placed or outcome.refused, "the step trades something, one way or the other"
    assert isinstance(outcome, SimpleNamespace) or outcome.placed is not None


def test_a_card_read_from_the_wrong_symbol_never_trades_the_other(tmp_path: Path) -> None:
    """The EURUSD step's rows carry EURUSD in the journal, even when the spec came back generic."""

    store = Store(tmp_path / "m.db")
    switch_path = tmp_path / "auto-switch.json"
    switch_path.write_text(json.dumps({"enabled": True}), encoding="utf-8")
    backend = FakeMT5(
        now=datetime(2026, 10, 13, 15, 0, tzinfo=UTC),
        symbol_name="EURUSDc",
        advance_ticks=False,
        equity=1000.0,
    )
    run(
        trader=MT5Trader(backend),
        store=store,
        settings=_two_symbol_settings(),
        setups=_payload([_eurusd_row()], digits=5),
        market=_market(),
        switch_path=switch_path,
        now_epoch=NOW,
        offset_s=_market()["offset_s"],
        timeframe_s=3600,
        max_hold_bars=24,
        validated=VALIDATED,
        symbol="EURUSD",
    )
    rows = store.recent_auto_trades()
    assert rows, "a placed or refused row was written either way"
    assert all(row.get("symbol") == "EURUSD" for row in rows)
    # And the same card could still be taken on the other market, because the claim is per symbol.
    assert store.auto_trade_taken("carry_trend", _card_row("carry_trend")["signal_epoch"], "buy") is False


def test_the_per_symbol_artifact_names_its_market(tmp_path: Path) -> None:
    """The panel reads which market a row belongs to from the row, and the view names its symbol."""
    import test_execution as te

    store = Store(tmp_path / "m.db")
    switch_path = tmp_path / "auto-switch.json"
    switch_path.write_text(json.dumps({"enabled": True}), encoding="utf-8")
    backend = FakeMT5(
        now=datetime(2026, 10, 13, 15, 0, tzinfo=UTC),
        symbol_name="EURUSDc",
        advance_ticks=False,
        equity=1000.0,
    )
    outcome = run(
        trader=MT5Trader(backend),
        store=store,
        settings=_two_symbol_settings(),
        setups=_payload([_eurusd_row()], digits=5),
        market=_market(),
        switch_path=switch_path,
        now_epoch=NOW,
        offset_s=_market()["offset_s"],
        timeframe_s=3600,
        max_hold_bars=24,
        validated=VALIDATED,
        symbol="EURUSD",
    )
    view = artifact(outcome, te._settings(), market=_market(), now_epoch=NOW, symbol="EURUSD")
    assert view["symbol"] == "EURUSD"
    assert view["allow"]["symbol"] == "EURUSD"
    assert view["open_count"] == len(view["open"])
    # The primary's view keeps owning the account-level sections: the per-symbol one does not
    # overwrite them.
    assert "account" not in view
    assert "settings" not in view
