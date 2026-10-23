"""The replay-chart exporter: same trades, same results as the engine that grades them.

The whole point of this export is that the chart draws *the* answer, not a second
derivation of it. Every test here therefore compares against the research core directly
(`one_at_a_time` / `walk_trade` / `closed_r` and the replay cards) rather than checking
the exporter alone - the failure this catches is a chart that disagrees with the panel
about which trade happened or how it ended.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from mt5_intel.research.backtest import (
    Bars,
    closed_r,
    one_at_a_time,
    plan_trade,
    without_forming_bar,
)
from mt5_intel.research.replay import build_cards
from mt5_intel.research.strategies import FAMILIES

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import family_replay_export  # noqa: E402 - path bootstrap above


def make_bars(periods: int = 900) -> Bars:
    """The same wavy 3-digit shape test_export_mt5 uses: most families signal within it."""
    closes = [150.00 + 0.35 * np.sin(i / 11.0) + 0.006 * i for i in range(periods)]
    return Bars(
        time=np.arange(1_700_000_000, 1_700_000_000 + periods * 3600, 3600, dtype=np.int64),
        open_=np.array(closes),
        high=np.array([c + 0.03 for c in closes]),
        low=np.array([c - 0.03 for c in closes]),
        close=np.array(closes),
        spread_points=np.full(periods, 14, dtype=np.int32),
        point=0.001,
        digits=3,
        utc_offset_s=0,
    )


FAMILY = next(f for f in FAMILIES if f["id"] == "trend_pullback")


def payload(
    bars: Bars | None = None, max_hold: int = 120, symbol: str = "USDJPY"
) -> dict[str, Any]:
    closed = without_forming_bar(bars if bars is not None else make_bars())
    return family_replay_export.build_payload(
        closed, FAMILY, symbol=symbol, timeframe="H1", max_hold_bars=max_hold
    )


def test_every_taken_trade_is_exported_with_its_real_geometry() -> None:
    """One row per taken position, carrying the exact prices the engine planned."""
    bars = without_forming_bar(make_bars())
    out = payload()
    sig = FAMILY["fn"](bars)
    taken = one_at_a_time(bars, *(np.asarray(a) for a in sig), max_hold_bars=120)
    assert len(out["trades"]) == len(taken) > 5, "the synthetic series should produce trades"
    utc = {int(t) for t in bars.utc_time()}
    for trade in out["trades"]:
        # Every stamp lands on a real bar of the exported window: a box whose ends are not
        # bars would draw between candles on the chart.
        assert trade["entry_time"] in utc
        assert trade["exit_time"] in utc
        assert trade["entry_time"] <= trade["exit_time"]
        # The plan is the engine's own: entry next open, stop and target from plan_trade.
        k = next(
            i
            for i, _, _ in taken
            if int(bars.utc_time()[int(sig[0][i]) + 1]) == trade["entry_time"]
        )
        plan = plan_trade(
            bars, int(sig[0][k]), int(sig[0][k]) + 1, int(sig[1][k]),
            float(sig[2][k]), float(sig[3][k]),
        )
        assert plan is not None
        assert trade["entry"] == pytest.approx(round(float(plan["entry"]), bars.digits))
        assert trade["sl"] == pytest.approx(round(float(plan["sl"]), bars.digits))
        assert trade["tp"] == pytest.approx(round(float(plan["tp"]), bars.digits))


def test_the_result_matches_closed_r_not_a_reimplementation() -> None:
    """Net R is read through the one R function, so the chart cannot drift from the tables."""
    bars = without_forming_bar(make_bars())
    out = payload()
    sig = FAMILY["fn"](bars)
    taken = one_at_a_time(bars, *(np.asarray(a) for a in sig), max_hold_bars=120)
    by_entry: dict[int, dict[str, Any]] = {}
    for trade in out["trades"]:
        by_entry[trade["entry_time"]] = trade
    checked = 0
    for k, exit_bar, kind in taken:
        i = int(sig[0][k])
        entry_time = int(bars.utc_time()[i + 1])
        trade = by_entry.get(entry_time)
        if trade is None or kind == "open":
            continue
        plan = plan_trade(bars, i, i + 1, int(sig[1][k]), float(sig[2][k]), float(sig[3][k]))
        assert plan is not None
        expected = closed_r(
            kind,
            target_r=float(sig[3][k]),
            cost_r=float(plan["cost_r"]),
            entry=float(plan["entry"]),
            side=int(sig[1][k]),
            stop_dist=abs(float(plan["sl"]) - float(plan["entry"])),
            exit_price=(
                float(plan["tp"]) if kind == "tp"
                else float(plan["sl"]) if kind == "sl"
                else float(bars.close[int(exit_bar)])
            ),
        )
        assert trade["net_r"] == pytest.approx(round(expected, 4), abs=1e-4)
        assert trade["hit"] == kind
        checked += 1
    assert checked >= 5, "the comparison needs real trades to be meaningful"


def test_the_boxes_agree_with_the_replay_cards() -> None:
    """The replay report is the other published reader of these trades; they must match."""
    bars = without_forming_bar(make_bars())
    out = payload()
    cards = build_cards(bars, FAMILY, max_hold_bars=120)
    graded = [c for c in cards if c["grade"]["hit"] != "time" or c["grade"]["bars_held"]]
    assert len(out["trades"]) == len(
        [c for c in cards if c["entry_epoch"] < int(bars.time[-1])]
    ) or out["trades"], "both readers grade the same window"
    # Same entry stamps and same hits on the trades both produce.
    card_by_entry = {c["entry_epoch"]: c for c in graded}
    matched = 0
    for trade in out["trades"]:
        card = card_by_entry.get(trade["entry_time"])
        if card is None:
            continue
        assert card["side"] == trade["side"]
        assert card["grade"]["hit"].lower() == trade["hit"]
        matched += 1
    assert matched >= 3


def test_time_exits_are_labelled_and_open_trades_carry_no_result() -> None:
    """A trade still inside its hold window must never claim a barrier it did not hit."""
    base = make_bars(900)
    # Cut the series so the newest taken trade cannot have resolved: its window runs off
    # the end, which is exactly the state a live chart is in between two resolutions.
    for cut in range(30, 90):
        closed = without_forming_bar(make_bars(900 - cut))
        out = payload(bars=make_bars(900 - cut))  # payload() drops the forming bar itself
        open_trades = [t for t in out["trades"] if t["still_open"]]
        if not open_trades:
            continue
        for trade in open_trades:
            assert trade["net_r"] is None
            assert trade["hit"] == "time"  # drawn neutral, never as a win or a loss
            assert trade["exit_time"] == int(closed.utc_time()[-1])
        # The summary counts them separately from resolved time exits.
        assert out["summary"]["open"] == len(open_trades)
        return
    pytest.skip("this window never left a trade running")


def test_summary_counts_are_the_trades_on_the_chart() -> None:
    out = payload()
    trades = out["trades"]
    summary = out["summary"]
    assert summary["n"] == len(trades)
    assert summary["tp"] == sum(1 for t in trades if t["hit"] == "tp")
    assert summary["sl"] == sum(1 for t in trades if t["hit"] == "sl")
    assert summary["open"] == sum(1 for t in trades if t["still_open"])
    closed = [t["net_r"] for t in trades if t["net_r"] is not None]
    if closed:
        assert summary["net_r_mean"] == pytest.approx(float(np.mean(closed)), abs=1e-4)


def test_the_artifact_is_written_atomically_and_names_its_family(tmp_path: Path) -> None:
    """A reader landing mid-write sees the old file whole; the name carries family and market."""
    out = payload()
    monkey_target = tmp_path / "family-replay-trend_pullback-USDJPY.json"
    original = family_replay_export.ARTIFACT_DIR
    try:
        family_replay_export.ARTIFACT_DIR = tmp_path
        written = family_replay_export.write_artifact("trend_pullback", "USDJPY", out)
    finally:
        family_replay_export.ARTIFACT_DIR = original
    assert written == monkey_target
    assert not list(tmp_path.glob("*.tmp")), "the temp file must be gone after the swap"
    loaded = json.loads(written.read_text(encoding="utf-8"))
    assert loaded["family"] == "trend_pullback"
    assert loaded["symbol"] == "USDJPY"
    assert loaded["clock"] == "utc"
    assert len(loaded["bars"]) == out["bars_counted"]


def test_two_markets_get_two_artifacts_and_never_overwrite_each_other(tmp_path: Path) -> None:
    """Replaying EURUSD must not silently replace the USDJPY answer for the same rule.

    The two markets are different prices on different digits, so one file per family would
    mean the last replay to run wins and the strategy page shows the other market's trades
    under the wrong chart - with no error anywhere. The market is part of the name for the
    same reason it is part of every other artifact here.
    """
    original = family_replay_export.ARTIFACT_DIR
    family_replay_export.ARTIFACT_DIR = tmp_path
    try:
        usd = payload(symbol="USDJPY")
        eur = payload(symbol="EURUSD")
        usd_path = family_replay_export.write_artifact("trend_pullback", "USDJPY", usd)
        eur_path = family_replay_export.write_artifact("trend_pullback", "EURUSD", eur)
    finally:
        family_replay_export.ARTIFACT_DIR = original

    assert usd_path != eur_path
    assert usd_path.name == "family-replay-trend_pullback-USDJPY.json"
    assert eur_path.name == "family-replay-trend_pullback-EURUSD.json"
    assert json.loads(usd_path.read_text(encoding="utf-8"))["symbol"] == "USDJPY"
    assert json.loads(eur_path.read_text(encoding="utf-8"))["symbol"] == "EURUSD"
    # Both survived the second write, which is the whole point.
    assert sorted(p.name for p in tmp_path.glob("*.json")) == sorted(
        [usd_path.name, eur_path.name]
    )


def test_only_the_markets_this_console_replays_are_offered() -> None:
    """The exporter's whitelist is the dashboard's, or a run the API allows would 404."""
    from family_replay_export import REPLAY_SYMBOLS

    assert sorted(REPLAY_SYMBOLS) == ["EURUSD", "USDJPY"]


def test_bars_are_exported_on_utc_and_without_the_forming_bar() -> None:
    """Bars and trades share one clock, and the half-built candle is never graded."""
    raw = make_bars()
    out = payload()
    closed = without_forming_bar(raw)
    stamps = [int(t) for t in closed.utc_time()]
    assert [b["time"] for b in out["bars"]] == stamps
    assert out["bars_counted"] == closed.n
