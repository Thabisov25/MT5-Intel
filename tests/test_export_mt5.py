"""Tests for the MT5 chart export: the format the indicator reads, and that it agrees with the panel.

The whole point of exporting rather than recomputing the rules in MQL5 is that there is one answer
to "what trades did these take". That only holds if the export and the Live tab read the same
decision, so the central test here compares them on one series instead of checking each alone.
"""

from __future__ import annotations

import csv
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from fakes import FakeMT5
from mt5_intel import watch
from mt5_intel.config import Settings
from mt5_intel.mt5_adapter import ReadOnlyMT5
from mt5_intel.research import export_mt5, setups
from mt5_intel.research.backtest import Bars
from mt5_intel.research.replay import families_for
from mt5_intel.research.strategies import Family as ReplayFamily
from mt5_intel.store import Store

WEDNESDAY_10_UTC = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)


def make_bars(periods: int = 900) -> Bars:
    """A wavy 3-digit series with a slow drift, so most families signal within it."""
    closes = [150.00 + 0.35 * np.sin(i / 11.0) + 0.006 * i for i in range(periods)]
    return Bars(
        time=np.arange(1_700_000_000, 1_700_000_000 + periods * 900, 900, dtype=np.int64),
        open_=np.array(closes),
        high=np.array([c + 0.03 for c in closes]),
        low=np.array([c - 0.03 for c in closes]),
        close=np.array(closes),
        spread_points=np.full(periods, 14, dtype=np.int32),
        point=0.001,
        digits=3,
        utc_offset_s=0,
    )


def _all(bars: Bars) -> list[dict[str, Any]]:
    return export_mt5.build_rows(bars, "M15", per_family=10_000)


def test_export_and_live_panel_report_the_same_trade() -> None:
    """One series, both readers: the row the chart draws must be the row the panel shows."""
    bars = make_bars()
    rows = _all(bars)
    assert rows, "the synthetic series should produce trades"
    checked = 0
    for family in families_for("M15"):
        panel = setups.latest_setup(bars, family)
        if panel["status"] in (setups.STATUS_NONE, setups.STATUS_ERROR):
            continue
        mine = [row for row in rows if row["family"] == family["id"]]
        assert mine, f"{family['id']} has a panel card but no exported row"
        last = mine[-1]
        if panel["status"] == setups.STATUS_OPEN:
            assert last["state"] == "open"
            assert last["result_r"] is None
        # The panel's row is the position as taken, and so is the chart's last row.
        assert last["entry"] == panel["entry"]
        assert last["sl"] == panel["sl"]
        assert last["tp"] == panel["tp"]
        assert last["resignals"] == panel["resignals"]
        assert last["bars_held"] == panel["bars_held"]
        if panel["status"] != setups.STATUS_OPEN:
            assert last["result_r"] == pytest.approx(panel["r_result"], abs=1e-3)
        checked += 1
    assert checked >= 3, "the comparison is worthless if almost nothing matched"


def test_export_keeps_one_row_per_position_not_one_per_bar() -> None:
    """The exporter reads the same flat-only decision (D-035), so a re-fire is not a row."""
    bars = make_bars()
    rows = _all(bars)
    trimmed: list[str] = []
    for family in families_for("M15"):
        signals = int(np.asarray(family["fn"](bars)[0]).size)
        mine = len([row for row in rows if row["family"] == family["id"]])
        assert mine <= signals
        if mine < signals:
            trimmed.append(family["id"])
    assert trimmed, "no family was trimmed: the export is not applying the flat-only rule"


def test_times_are_the_bars_own_clock_not_utc() -> None:
    """The indicator matches rows to bars by stamp, so the stamp has to be the chart's clock."""
    bars = make_bars()
    rows = _all(bars)
    assert rows
    bar_stamps = {export_mt5._stamp(int(t)) for t in bars.time}
    for row in rows:
        stamp = str(row["entry_time"])
        assert len(stamp) == len("2026.10.12 15:00")
        # A timezone conversion anywhere in the path would put this stamp off a real bar.
        assert stamp in bar_stamps


def truncated(base: Bars, cut: int) -> Bars:
    """The series cut `cut` bars short, so what was closed is now running."""
    return Bars(
        time=base.time[: base.n - cut],
        open_=base.open_[: base.n - cut],
        high=base.high[: base.n - cut],
        low=base.low[: base.n - cut],
        close=base.close[: base.n - cut],
        spread_points=base.spread_points[: base.n - cut],
        point=base.point,
        digits=base.digits,
        utc_offset_s=0,
    )


def test_a_held_trades_newest_signal_reaches_the_chart() -> None:
    """A re-fire while a rule holds a trade is a current reading, not absence.

    This is the chart-side half of the same contract the engine now honours: the held trade is
    drawn as taken, and the rule's newest stood-aside signal is drawn beside it as state
    `signal` - what the rule is asking for NOW - so a conviction you can see on the panel is one
    the chart shows too, without waiting for the live trade to end.
    """
    base = make_bars(900)
    # Suffling the cut until some family holds a trade AND has a newer re-fire; the wavy series
    # produces both within a few cuts.
    found = False
    for cut in range(60):
        rows = export_mt5.build_rows(truncated(base, cut), "M15", per_family=10_000)
        signals = [row for row in rows if row["state"] == "signal"]
        if not signals:
            continue
        found = True
        for row in signals:
            # It is a plan, not a taken trade: no exit, no result, and its own side/levels.
            assert row["exit_time"] == ""
            assert row["result_r"] is None
            assert row["side"] in ("buy", "sell")
            bar_stamps = {export_mt5._stamp(int(t)) for t in truncated(base, cut).time}
            assert str(row["entry_time"]) in bar_stamps
        # Confluence counts taken trades, never a plan.
        assert all(row["confluence"] == 1 for row in signals)
        break
    assert found, "the truncated series never produced a current-signal row to check"


def test_a_flat_rules_take_now_signal_reaches_the_chart(monkeypatch: pytest.MonkeyPatch) -> None:
    """A flat rule whose newest signal is on the last closed bar asks for now - and draws it.

    Before the closed-bar convention the chart only drew a stood-aside signal beside a held
    trade, so the very arrow a rule was asking for as its take-now was the one a flat rule could
    never draw. The row is a plan (state `signal`), planned at the forming candle's open, and it
    must never be a taken trade: no exit, no result, and it cannot be walked inside the candle.
    """
    # A family that fires exactly on the newest CLOSED bar of whatever history it is given: its
    # entry is the forming candle's open, the price a market order would meet now.
    def fn(view: Bars) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        fire = np.array([view.n - 1], dtype=np.int64)
        return fire, np.array([1], dtype=np.int64), np.array([10.0]), np.array([2.0])

    family = {"id": "synthetic_now", "description": "test family", "fn": fn}
    monkeypatch.setattr(export_mt5, "families_for", lambda timeframe: [family])
    bars = make_bars(60)
    rows = export_mt5.build_rows(bars, "M15", per_family=10_000)
    # The family is flat (its single signal has no closed bar to enter on), so the row is the
    # take-now reading and nothing else.
    assert [row["state"] for row in rows] == ["signal"]
    plan = rows[0]
    assert plan["exit_time"] == ""
    assert plan["result_r"] is None
    # A long entry at the forming bar's open: the newest bar's own stamp, so the arrow sits on
    # the live candle the chart is drawing. (The row is rounded to the quote's digits.)
    assert float(plan["entry"]) == pytest.approx(float(bars.open_[-1]), abs=0.0005)
    assert str(plan["entry_time"]) == export_mt5._stamp(int(bars.time[bars.n - 1]))


def test_a_signal_row_is_never_counted_as_a_trade() -> None:
    """A current-signal row is neither the family's trade count nor its live one."""
    base = make_bars(900)
    for cut in range(60):
        bars = truncated(base, cut)
        rows = export_mt5.build_rows(bars, "M15", per_family=10_000)
        signals = [row for row in rows if row["state"] == "signal"]
        if not signals:
            continue
        for sig in signals:
            taken = [row for row in rows if row["family"] == sig["family"] and row["state"] != "signal"]
            live_taken = [row for row in taken if row["state"] == "open"]
            # The signal row is additional to the taken rows, and the family's live trade is
            # still present exactly as one_at_a_time took it.
            assert sig not in taken
            if live_taken:
                assert live_taken[-1]["entry"] != sig["entry"] or live_taken[-1]["side"] == sig["side"]
        break


def test_csv_round_trips_with_the_header_the_indicator_expects(tmp_path: Path) -> None:
    rows = _all(make_bars())
    path = tmp_path / "mt5intel_M15.csv"
    export_mt5.write_csv(rows, path)
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == list(export_mt5.HEADER)
        assert len(export_mt5.HEADER) == 12  # 11 the indicator requires, plus the agreement
        parsed = list(reader)
    assert len(parsed) == len(rows)
    for row in parsed:
        assert row["state"] in ("open", "closed")
        if row["state"] == "open":
            assert row["exit_time"] == "" and row["result_r"] == ""
        else:
            assert row["exit_time"] != "" and row["result_r"] != ""


def test_line_values_are_the_same_averages_the_panel_quotes() -> None:
    """One calculation, two readers: the chart line and the number beside it must agree.

    This is the reason the lines are exported instead of being recomputed with MT5's own iMA():
    if the two ever disagreed there would be no way to tell which one the rules used.
    """
    bars = make_bars()
    rows = export_mt5.build_lines(bars, window=50)
    assert rows
    ctx = setups.market_context(bars)
    last_closed = rows[-2]  # the forming bar is exported too; the reading stops one bar short
    assert float(last_closed["ema_fast"]) == pytest.approx(ctx["ema_fast"], abs=1e-6)
    assert float(last_closed["ema_slow"]) == pytest.approx(ctx["ema_slow"], abs=1e-6)
    assert float(last_closed["sma_fast"]) == pytest.approx(ctx["sma_fast"], abs=1e-6)
    assert float(last_closed["sma_slow"]) == pytest.approx(ctx["sma_slow"], abs=1e-6)
    # The forming bar is included, so a trader's average sits on the live candle.
    assert rows[-1]["time"] == export_mt5._stamp(int(bars.time[-1]))


def test_line_rows_all_land_on_real_bars() -> None:
    """The indicator counts rows back from the newest bar, so a stamp off a bar breaks the line."""
    bars = make_bars()
    rows = export_mt5.build_lines(bars)
    assert len(rows) == min(export_mt5.DEFAULT_LINE_BARS, bars.n)
    stamps = [export_mt5._stamp(int(t)) for t in bars.time]
    assert [row["time"] for row in rows] == stamps[-len(rows):]
    assert all(row["ema_slow"] for row in rows[200:]), "the slow average should be warm by now"


def test_lines_leave_a_cold_average_blank_rather_than_guessing() -> None:
    """A blank cell is a warm-up bar, never a hole: a hole would break the drawn line."""
    bars = make_bars(260)
    rows = export_mt5.build_lines(bars)
    for key, period in (("sma_fast", 20), ("ema_fast", 50), ("sma_slow", 50), ("ema_slow", 200)):
        blanks = [i for i, row in enumerate(rows) if row[key] == ""]
        assert blanks == list(range(period - 1)), f"{key} should be blank only before it warms up"
    for row in rows[200:]:
        for key in export_mt5.LINES_HEADER[1:]:
            assert float(row[key]) > 0


def test_context_carries_the_numbers_the_panel_prints() -> None:
    """The panel on the chart reads these keys; a rename here would blank a line there."""
    bars = make_bars()
    rows = dict(export_mt5.build_context(bars, "H1", "USDJPYc", offset_s=3 * 3600))
    ctx = setups.market_context(bars, 3 * 3600)
    for key in ("symbol", "timeframe", "as_of", "clock", "trend", "ema_fast", "ema_slow",
                "ema_sep_pips", "ema_sep_side", "sma_fast", "sma_slow", "atr_pips", "rsi",
                "range_low", "range_high", "range_bars", "range_pos_pct", "fresh_high",
                "chg_24", "chg_96", "chg_240"):
        assert key in rows, f"the panel prints {key}"
    assert rows["trend"] == ctx["ema_trend"]
    assert rows["as_of"].endswith("UTC")
    assert rows["ema_fast"] == f"{ctx['ema_fast']:.{bars.digits}f}"


def test_context_says_so_when_the_clock_offset_is_unknown() -> None:
    """A stamp labelled UTC that is not UTC would be worse than no label at all."""
    bars = make_bars()
    rows = dict(export_mt5.build_context(bars, "H1", "USDJPYc"))
    assert rows["clock"] == "server"
    assert rows["as_of"].endswith("broker clock")
    assert rows["trend"] in ("up", "down")


def test_export_writes_all_three_files_side_by_side(tmp_path: Path) -> None:
    """The chart reads three files by name; writing only some of them is a broken chart."""
    bars = make_bars()
    backend = FakeMT5(now=WEDNESDAY_10_UTC, symbol_name="USDJPY", data_path=str(tmp_path))
    adapter = ReadOnlyMT5(backend)
    path = export_mt5.export(adapter, bars, "USDJPY", "H1", line_bars=120)
    assert path is not None
    folder = tmp_path / "MQL5" / "Files"
    assert sorted(p.name for p in folder.iterdir()) == [
        "mt5intel_USDJPY_H1.csv",
        "mt5intel_USDJPY_H1_context.csv",
        "mt5intel_USDJPY_H1_lines.csv",
    ]
    with (folder / "mt5intel_USDJPY_H1_lines.csv").open(encoding="utf-8", newline="") as handle:
        parsed = list(csv.DictReader(handle))
    assert list(parsed[0]) == list(export_mt5.LINES_HEADER)
    assert len(parsed) == 120
    with (folder / "mt5intel_USDJPY_H1_context.csv").open(encoding="utf-8", newline="") as handle:
        ctx_rows = list(csv.reader(handle))
    assert ctx_rows[0] == ["key", "value"]
    assert all(len(row) == 2 for row in ctx_rows[1:])


def test_the_indicator_derives_the_same_file_names() -> None:
    """The sibling names live in two languages; this is the seam where they have to agree."""
    source = Path(export_mt5.__file__).resolve().parents[3] / "tools" / "mt5"
    text = (source / "Mt5IntelSignals.mq5").read_text(encoding="utf-8")
    for suffix in ("_lines", "_context"):
        assert f'SiblingName(FileName(), "{suffix}")' in text, f"the indicator looks for {suffix}"
    # The trades CSV keeps its column order, and the lines file is one time column plus one
    # column per plot: the count in MQL5 has to match the count written here.
    assert "COLS   11" in text
    # The agreement column is optional on the indicator's side, so its index has to match the
    # constant there: a count read from the wrong position would paint a trade that never agreed.
    assert f"#define COL_CONFLUENCE {export_mt5.HEADER.index('confluence')}" in text
    assert f"#property indicator_buffers {len(export_mt5.LINES_HEADER) - 1}" in text
    # The names are per symbol, and the indicator derives them from its own chart: a chart of one
    # symbol cannot open another symbol's trades file and draw it as its own. The two strings the
    # derivation is built from have to be exactly what this side writes.
    assert 'return "mt5intel_" + _Symbol + "_" + ChartTimeframe() + ".csv";' in text
    assert export_mt5.filename("H1", "EURUSD") == "mt5intel_EURUSD_H1.csv"
    assert (
        export_mt5.lines_filename("H1", "EURUSD")
        == "mt5intel_EURUSD_H1"
        + "_lines.csv"  # the same base the indicator's SiblingName builds from FileName()
    )
    assert export_mt5.context_filename("H1", "EURUSD") == "mt5intel_EURUSD_H1_context.csv"


def test_the_indicator_alerts_on_a_rule_that_exists() -> None:
    """A default the indicator cannot match notifies nothing, and says nothing about it.

    The alert names a rule by its id, and the ids are decided here, beside the rules themselves.
    Rename one in Python and the chart would keep drawing that family while the alert stopped
    arriving - with nothing on screen to say so, which is the failure this seam is here to catch.
    """
    source = Path(export_mt5.__file__).resolve().parents[3] / "tools" / "mt5"
    text = (source / "Mt5IntelSignals.mq5").read_text(encoding="utf-8")
    assert 'InpNotifyRules   = "trend_pullback"' in text
    assert "trend_pullback" in {family["id"] for family in families_for("H1")}
    # Both halves of "an alert": the terminal's own popup, and the push that reaches a phone.
    assert "Alert(text);" in text
    assert "SendNotification(text)" in text


def test_chart_path_needs_a_terminal_data_path() -> None:
    class NoPath:
        def terminal_data_path(self) -> None:
            return None

    class WithPath:
        def terminal_data_path(self) -> str:
            return "C:\\Terminal"

    no_path: Any = NoPath()
    with_path: Any = WithPath()
    assert export_mt5.chart_path(no_path, "H1", None, "USDJPY") is None
    target = export_mt5.chart_path(with_path, "H1", None, "USDJPY")
    assert target is not None
    assert target.name == "mt5intel_USDJPY_H1.csv"
    assert target.parent.name == "Files"


def test_a_rule_that_raises_does_not_cost_the_others_their_chart() -> None:
    def boom(_bars: Bars) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        raise RuntimeError("boom")

    broken: ReplayFamily = {"id": "broken", "description": "raises", "fn": boom}
    bars = make_bars(300)
    assert export_mt5._family_signals(bars, broken) is None
    assert all(export_mt5._family_signals(bars, family) is not None for family in families_for("M15"))


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        login=12345678,
        password="unit-test-secret",
        server="HFMarketsSV-Demo",
        terminal_path=None,
        utc_offset_hours=None,
        symbol_root="USDJPY",
        reference_equity_zar=100.0,
        risk_pct=1.0,
        usd_zar_fallback=18.0,
        history_bars=600,
        spread_days=2,
        tick_watch_seconds=0.0,
        report_dir=tmp_path,
    )


def test_watcher_writes_the_chart_file_from_the_same_bars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The watcher keeps the chart current on its own timer, from the bars it already read."""
    bars = make_bars()
    backend = FakeMT5(now=WEDNESDAY_10_UTC, symbol_name="USDJPY", data_path=str(tmp_path))
    adapter = ReadOnlyMT5(backend)
    settings = _settings(tmp_path)
    store = Store(tmp_path / "mt5.db")
    monkeypatch.setattr(watch, "SETUPS_FILE", tmp_path / "setups.json")

    calls = {"n": 0}

    def counted(*_args: object, **_kwargs: object) -> Bars:
        calls["n"] += 1
        return bars

    monkeypatch.setattr(watch, "load_bars", counted)
    # The refresh windows are measured against the artifacts' own mtimes, so this clock is real.
    now = int(time.time())

    watch._refresh_setups(adapter, settings, watch._read_state(adapter, "USDJPY"), store, now)
    folder = tmp_path / "MQL5" / "Files"
    # All three, named for the symbol they belong to: an arrow with no lines under it is half a
    # chart, and an arrow another symbol could also read is worse.
    assert sorted(p.name for p in folder.iterdir()) == [
        "mt5intel_USDJPY_H1.csv",
        "mt5intel_USDJPY_H1_context.csv",
        "mt5intel_USDJPY_H1_lines.csv",
    ]
    csv_file = folder / "mt5intel_USDJPY_H1.csv"
    with csv_file.open(encoding="utf-8", newline="") as handle:
        parsed = list(csv.DictReader(handle))
    assert parsed
    assert set(parsed[0]) == set(export_mt5.HEADER)
    with (folder / "mt5intel_USDJPY_H1_lines.csv").open(encoding="utf-8", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == len(export_mt5.build_lines(bars))
    with (folder / "mt5intel_USDJPY_H1_context.csv").open(encoding="utf-8", newline="") as handle:
        context_rows = dict(csv.reader(handle))
    assert context_rows["symbol"] == "USDJPY"
    assert context_rows["timeframe"] == "H1"
    assert calls["n"] == 1, "panel and chart must share one history read"

    # Fresh on all counts: nothing is rewritten, and no history is re-read.
    csv_file.write_text("sentinel", encoding="utf-8")
    watch._refresh_setups(adapter, settings, watch._read_state(adapter, "USDJPY"), store, now + 10)
    assert csv_file.read_text(encoding="utf-8") == "sentinel"

    # Past the chart's window but with a still-fresh panel, the chart catches up on its own.
    (tmp_path / "setups.json").write_text(json.dumps({"setups": []}), encoding="utf-8")
    watch._refresh_setups(
        adapter,
        settings,
        watch._read_state(adapter, "USDJPY"),
        store,
        now + watch.CHART_EXPORT_S + 1,
    )
    assert csv_file.read_text(encoding="utf-8").startswith("family,side,entry_time")


def _rows(*specs: tuple[str, str, str, str]) -> list[dict[str, Any]]:
    """Trade rows as the exporter builds them, for the agreement rule on its own."""
    return [
        {"family": family, "side": side, "entry_time": stamp, "state": state}
        for family, side, stamp, state in specs
    ]


def test_two_live_cards_on_one_bar_are_marked_as_agreeing() -> None:
    """The reading the chart paints green: two running cards, one bar, one direction."""
    rows = _rows(
        ("trend_pullback", "buy", "2026.10.12 15:00", "open"),
        ("pullback_scalp", "buy", "2026.10.12 15:00", "open"),
        ("ma_crossover", "sell", "2026.10.12 15:00", "open"),
        ("rsi_mean_reversion", "buy", "2026.10.12 14:00", "open"),
    )
    export_mt5._mark_confluence(rows)
    # The sell on the same bar is a disagreement, not an agreement; the older bar stands alone.
    assert [row["confluence"] for row in rows] == [2, 2, 1, 1]


def test_three_rules_agreeing_are_counted_three_times() -> None:
    rows = _rows(
        ("trend_pullback", "buy", "2026.10.12 15:00", "open"),
        ("pullback_scalp", "buy", "2026.10.12 15:00", "open"),
        ("sweep_reclaim", "buy", "2026.10.12 15:00", "open"),
    )
    export_mt5._mark_confluence(rows)
    assert [row["confluence"] for row in rows] == [3, 3, 3]


def test_a_finished_trade_never_counts_as_agreeing() -> None:
    """A closed card is not live alongside anything, so it can neither give nor take agreement."""
    rows = _rows(
        ("sweep_reclaim", "buy", "2026.10.12 15:00", "closed"),
        ("trend_pullback", "buy", "2026.10.12 15:00", "open"),
        ("pullback_scalp", "buy", "2026.10.12 15:00", "open"),
    )
    export_mt5._mark_confluence(rows)
    assert [row["confluence"] for row in rows] == [1, 2, 2]


def test_exported_agreement_matches_the_live_rows_on_the_same_bar() -> None:
    """The count means one thing, on real exported rows: the live cards on that bar and side."""
    # 400 bars is a fixture that leaves two rules still running, which is what this needs.
    rows = export_mt5.build_rows(make_bars(400), "M15", per_family=10_000)
    live = [row for row in rows if row["state"] == "open"]
    assert live, "the synthetic series should leave something running"
    for row in live:
        same = [
            other
            for other in live
            if (other["entry_time"], other["side"]) == (row["entry_time"], row["side"])
        ]
        assert row["confluence"] == len({other["family"] for other in same})
        assert row["confluence"] >= 1
    for row in rows:
        if row["state"] == "closed":
            assert row["confluence"] == 1


def test_the_written_csv_carries_one_agreement_count_per_row(tmp_path: Path) -> None:
    """What the indicator reads: an integer on every row, including the ones standing alone."""
    path = tmp_path / "mt5intel_M15.csv"
    export_mt5.write_csv(_all(make_bars()), path)
    with path.open(encoding="utf-8", newline="") as handle:
        parsed = list(csv.DictReader(handle))
    assert parsed
    assert all(int(row["confluence"]) >= 1 for row in parsed)
