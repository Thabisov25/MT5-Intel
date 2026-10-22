"""The trade journal: position and deal rows in, one priced trade view out.

The fake rows here are shaped like the terminal's own - a position with ``type``, ``price_open``
and ``profit``, a deal with ``position_id``, ``entry``, ``profit``, ``swap`` and ``commission`` -
so a test that passes is a statement about the mapping the watcher relies on, not about a fixture.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mt5_intel import trades

NOW = int(datetime(2026, 10, 13, 12, 0, tzinfo=UTC).timestamp())
OPEN_EPOCH = NOW - 3 * 3600
CLOSE_EPOCH = NOW - 3600
SERVER_OFFSET_S = 3 * 3600


def position(**overrides: Any) -> SimpleNamespace:
    row: dict[str, Any] = {
        "ticket": 500,
        "symbol": "USDJPYc",
        "type": 0,
        "volume": 0.10,
        "price_open": 150.100,
        "price_current": 150.250,
        "sl": 149.900,
        "tp": 150.500,
        "profit": 15.0,
        "swap": -0.5,
        "time": OPEN_EPOCH,
        "magic": 7,
        "comment": "sweep",
    }
    row.update(overrides)
    return SimpleNamespace(**row)


def deal(**overrides: Any) -> SimpleNamespace:
    row: dict[str, Any] = {
        "position_id": 600,
        "entry": 0,
        "type": 0,
        "price": 151.000,
        "volume": 0.10,
        "profit": 0.0,
        "swap": 0.0,
        "commission": -1.5,
        "time": OPEN_EPOCH,
        "symbol": "USDJPYc",
    }
    row.update(overrides)
    return SimpleNamespace(**row)


def setups(*families: str, asof: str = "2026-10-13 09:00") -> dict[str, Any]:
    rows = [
        {"family": family, "status": "open", "side": "buy", "entry": 150.1, "sl": 149.9, "tp": 150.5}
        for family in families
    ]
    rows.append({"family": "ma_crossover", "status": "stop", "side": "sell"})
    return {"asof_bar_utc": asof, "setups": rows}


def build(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "positions": (),
        "deals": (),
        "watched_symbol": "USDJPY",
        "digits_by_symbol": {"USDJPYc": 3},
        "offset_s": SERVER_OFFSET_S,
        "account": 49869754,
        "currency": "ZAR",
        "now_epoch": NOW,
    }
    kwargs.update(overrides)
    return trades.build(**kwargs)


def test_open_position_is_marked_to_the_terminals_own_result() -> None:
    """The floating row is the terminal's number: profit, swap and the commission it charged."""
    payload = build(
        positions=(position(),),
        deals=(deal(position_id=500, entry=0, price=150.100, commission=-1.5),),
        setups=setups("sweep_reclaim"),
    )
    assert payload["open_count"] == 1
    assert payload["closed_count"] == 0
    row = payload["trades"][0]
    assert row["status"] == "open"
    assert row["side"] == "buy"
    assert row["entry"] == 150.1
    assert row["price_now"] == 150.25
    assert row["net"] == pytest.approx(15.0 - 0.5 - 1.5)
    assert payload["open_net"] == pytest.approx(13.0)


def test_closed_position_is_rebuilt_from_the_deals_that_closed_it() -> None:
    """One trade, not one row per deal: entry, exit and result are the deals added up."""
    payload = build(
        deals=(
            deal(position_id=600, entry=0, type=0, price=151.000, commission=-1.5),
            deal(
                position_id=600,
                entry=1,
                type=1,
                price=150.800,
                profit=20.0,
                swap=-0.5,
                commission=-1.5,
                time=CLOSE_EPOCH,
            ),
        )
    )
    assert payload["closed_count"] == 1
    row = payload["trades"][0]
    assert row["status"] == "closed"
    assert row["side"] == "buy"
    assert row["entry"] == 151.0
    assert row["exit"] == 150.8
    assert row["net"] == pytest.approx(20.0 - 0.5 - 1.5 - 1.5)
    assert row["deals"] == 2
    # The exit is stamped when the closing deal happened, on the UTC clock.
    assert row["closed_utc"] == trades._stamp(CLOSE_EPOCH, SERVER_OFFSET_S)
    assert payload["closed_net"] == pytest.approx(16.5)
    assert (payload["wins"], payload["losses"]) == (1, 0)
    assert payload["win_rate"] == 1.0


def test_a_partly_closed_position_stays_open_with_its_realised_result_included() -> None:
    """A position with one foot already out is still the account's position, and priced as one."""
    payload = build(
        positions=(position(),),
        deals=(
            deal(position_id=500, entry=0, price=150.100, commission=-1.5),
            deal(
                position_id=500,
                entry=1,
                type=1,
                price=150.200,
                volume=0.05,
                profit=5.0,
                commission=-0.75,
                time=CLOSE_EPOCH,
            ),
        ),
    )
    assert payload["open_count"] == 1
    assert payload["closed_count"] == 0, "the position is still open; it is not also a closed trade"
    assert payload["trades"][0]["net"] == pytest.approx(15.0 - 0.5 + 5.0 - 1.5 - 0.75)


def test_a_position_whose_opening_deal_has_aged_out_reports_no_entry() -> None:
    """A window that starts after the entry can still say what closed and for how much."""
    payload = build(
        deals=(
            deal(
                position_id=700,
                entry=1,
                type=1,
                price=150.800,
                profit=-12.0,
                commission=-1.5,
                time=CLOSE_EPOCH,
            ),
        )
    )
    row = payload["trades"][0]
    assert row["entry"] is None
    assert row["side"] == "buy", "the closing deal's type names the side it closed"
    assert row["exit"] == 150.8
    assert row["net"] == pytest.approx(-13.5)


def test_a_short_position_is_read_as_a_sell() -> None:
    payload = build(
        deals=(
            deal(position_id=800, entry=0, type=1, price=150.000, commission=-1.0),
            deal(
                position_id=800,
                entry=1,
                type=0,
                price=149.500,
                profit=9.0,
                commission=-1.0,
                time=CLOSE_EPOCH,
            ),
        )
    )
    assert payload["trades"][0]["side"] == "sell"
    assert payload["trades"][0]["entry"] == 150.0


def test_an_open_trade_keeps_the_context_captured_when_it_was_first_seen() -> None:
    """The point of the carry-forward: a trade taken on a live card still names that card later."""
    previous = {
        "trades": [
            {
                "ticket": 500,
                "status": "open",
                "context": [{"family": "sweep_reclaim", "side": "buy", "entry": 150.1}],
                "context_utc": "2026-10-13 08:00",
                "context_captured_utc": "2026-10-13 08:05",
                "comment": "sweep",
                "magic": 7,
            }
        ]
    }
    payload = build(
        positions=(position(),),
        deals=(),
        previous=previous,
        # The cards on offer *now* are different ones: the row must not adopt them.
        setups=setups("compression_breakout"),
    )
    row = payload["trades"][0]
    assert [entry["family"] for entry in row["context"]] == ["sweep_reclaim"]
    assert row["context_utc"] == "2026-10-13 08:00"
    assert row["context_captured_utc"] == "2026-10-13 08:05"


def test_a_new_trade_captures_the_cards_live_at_the_moment_it_is_first_seen() -> None:
    payload = build(
        positions=(position(),),
        deals=(),
        setups=setups("sweep_reclaim", "trend_pullback"),
    )
    row = payload["trades"][0]
    assert sorted(entry["family"] for entry in row["context"]) == ["sweep_reclaim", "trend_pullback"]
    assert row["context_utc"] == "2026-10-13 09:00"
    assert row["context_captured_utc"] == trades._stamp(NOW, 0)
    assert row["context"][0]["sl"] == 149.9, "the card is kept with the numbers it was taken on"


def test_context_is_captured_for_a_suffixed_symbol_too() -> None:
    """The broker's USDJPYc is the watched USDJPY: a suffix must not empty the context."""
    payload = build(positions=(position(symbol="USDJPYc"),), setups=setups("sweep_reclaim"))
    assert [entry["family"] for entry in payload["trades"][0]["context"]] == ["sweep_reclaim"]


def test_context_is_not_borrowed_from_another_symbols_card() -> None:
    """A card for USDJPY says nothing about a EURUSD trade, so it is not attached to one."""
    payload = build(
        positions=(position(symbol="EURUSDc"),),
        setups=setups("sweep_reclaim"),
        digits_by_symbol={"EURUSDc": 5},
    )
    row = payload["trades"][0]
    assert row["context"] == []
    assert row["context_utc"] is None
    assert row["context_captured_utc"] is not None, "it was still first seen at this moment"


def test_open_trades_come_first_and_closed_ones_newest_first() -> None:
    older = deal(position_id=601, entry=0, price=151.0)
    older_out = deal(
        position_id=601, entry=1, type=1, price=150.5, profit=1.0, time=CLOSE_EPOCH - 7200
    )
    newer_out = deal(
        position_id=602, entry=1, type=1, price=149.0, profit=-4.0, time=CLOSE_EPOCH
    )
    payload = build(positions=(position(),), deals=(older, older_out, newer_out))
    assert [row["status"] for row in payload["trades"]] == ["open", "closed", "closed"]
    assert [row["ticket"] for row in payload["trades"]] == [500, 602, 601]


def test_a_broken_deal_row_cannot_take_the_journal_down_with_it() -> None:
    """One unreadable cell defaults to nothing, rather than raising inside the watch cycle."""
    payload = build(
        positions=(position(profit=None, swap="not a number", volume=None),),
        deals=(
            deal(position_id=900, entry=0, price=None, time=None, volume=None),
            deal(position_id=900, entry=1, type=1, price=150.0, profit=2.0, time=CLOSE_EPOCH),
        ),
    )
    row = next(r for r in payload["trades"] if r["ticket"] == 500)
    assert row["profit"] == 0.0
    assert row["swap"] == 0.0
    assert row["net"] == 0.0
    closed = next(r for r in payload["trades"] if r["ticket"] == 900)
    assert closed["status"] == "closed"
    assert closed["entry"] is None
    assert closed["opened_utc"] is None


def test_no_terminal_history_reads_as_nothing_rather_than_an_error() -> None:
    payload = build(deals=None, positions=None)
    assert payload["trades"] == []
    assert payload["open_count"] == 0
    assert payload["win_rate"] is None


def test_stamps_are_reported_on_the_utc_clock_once_the_broker_offset_is_known() -> None:
    payload = build(positions=(position(),), offset_s=SERVER_OFFSET_S)
    row = payload["trades"][0]
    expected = datetime.fromtimestamp(OPEN_EPOCH - SERVER_OFFSET_S, UTC).strftime("%Y-%m-%d %H:%M")
    assert row["opened_utc"] == expected
    assert payload["clock"] == "utc"


def test_an_unknown_clock_is_labelled_rather_than_assumed_to_be_utc() -> None:
    payload = build(positions=(position(),), offset_s=None)
    assert payload["clock"] == "server"


def test_the_window_asks_for_a_lookback_plus_a_day_of_slack() -> None:
    start, end = trades.fetch_window(NOW, days=30)
    assert start == NOW - 30 * 86_400
    assert end == NOW + trades.WINDOW_SLACK_S


def test_load_artifact_tolerates_a_missing_or_half_written_file(tmp_path: Path) -> None:
    assert trades.load_artifact(tmp_path / "nothing.json") is None
    half = tmp_path / "trades.json"
    half.write_text('{"trades": [', encoding="utf-8")
    assert trades.load_artifact(half) is None
    half.write_text('["not an object"]', encoding="utf-8")
    assert trades.load_artifact(half) is None
    half.write_text('{"trades": []}', encoding="utf-8")
    assert trades.load_artifact(half) == {"trades": []}
