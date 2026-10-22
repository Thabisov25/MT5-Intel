"""Tests for the live per-strategy view: the position in place, live or closed, and the backdrop.

The point of the module is the open/closed distinction the grader throws away (it always reports
an exit), so that distinction is what most of these pin down: a walk that reaches no barrier must
read as live, and each barrier must read as the exit it is. The rest pin down D-035 - that the
row is the trade as taken, not the newest re-fire of it.
"""

from __future__ import annotations

import inspect
import re
from datetime import UTC, datetime

import numpy as np
import pytest

from mt5_intel.research import setups
from mt5_intel.research.backtest import Bars
from mt5_intel.research.replay import families_for
from mt5_intel.research.strategies import Family, _atr, _ema, ma_crossover

STAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")


def utc_str(epoch: int, offset_s: int) -> str:
    """The bar's instant in UTC, computed here rather than borrowed from the module under test."""
    return datetime.fromtimestamp(epoch - offset_s, UTC).strftime("%Y-%m-%d %H:%M")


def make_bars(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    *,
    spread_points: int = 10,
) -> Bars:
    """Synthetic series with the same 3-digit quote as the live symbol (pip = 0.01)."""
    n = len(closes)
    return Bars(
        time=np.arange(1_700_000_000, 1_700_000_000 + n * 900, 900, dtype=np.int64),
        open_=np.array(closes),
        high=np.array(highs),
        low=np.array(lows),
        close=np.array(closes),
        spread_points=np.full(n, spread_points, dtype=np.int32),
        point=0.001,
        digits=3,
        utc_offset_s=0,
    )


def always(sig: list[int], side: list[int], stop_pips: float = 10.0, tp_r: float = 2.0) -> Family:
    """A family that fires the given signals, so the walk is the only thing under test."""

    def fn(bars: Bars) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        sig_i = np.array(sig, dtype=np.int64)
        sd = np.array(side, dtype=np.int64)
        return sig_i, sd, np.full(sig_i.size, stop_pips), np.full(sig_i.size, tp_r)

    return {"id": "synthetic", "description": "test family", "fn": fn}


def _boom(bars: Bars) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    raise RuntimeError("boom")


# Entry at bar 1 open (1.502) with a 10-pip stop: SL 1.402, TP 1.702 at 2R.
_ENTRY = 1.502
_STOP_DIST = 0.10
# Median snapshot spread 10 points on a 3-digit symbol is 1 pip, so 10 pips of risk costs 0.2R.
_COST_R = 0.2


def test_card_with_no_barrier_reached_is_live() -> None:
    bars = make_bars(
        [1.500, 1.502, 1.520, 1.530, 1.540],
        [1.501, 1.503, 1.525, 1.535, 1.545],
        [1.499, 1.501, 1.515, 1.525, 1.535],
    )
    row = setups.latest_setup(bars, always([0], [1]))
    assert row["status"] == setups.STATUS_OPEN
    assert row["side"] == "buy"
    assert row["entry"] == _ENTRY
    assert row["sl"] == 1.402
    assert row["tp"] == 1.702
    assert row["bars_held"] == 3  # every bar after the entry bar was walked
    assert row["r_result"] is None
    # (1.540 - 1.502) / 0.10 = 0.38R, less the round-trip cost charged in R.
    assert abs(row["r_now"] - (0.38 - _COST_R)) < 1e-9
    assert abs(row["r_to_stop"] - 1.38) < 1e-9
    assert abs(row["r_to_target"] - 1.62) < 1e-9
    assert row["closed_utc"] is None
    assert row["age_s"] == 4 * 900
    assert row["bars_since_signal"] == 4


def test_target_reached_reports_a_closed_card() -> None:
    bars = make_bars(
        [1.500, 1.502, 1.520, 1.700, 1.700],
        [1.501, 1.503, 1.525, 1.710, 1.706],
        [1.499, 1.501, 1.515, 1.690, 1.692],
    )
    # The last bar is the forming candle, so it carries no verdict: the target was confirmed by
    # the bar before it, the way the live view reads a terminal feed.
    row = setups.latest_setup(bars, always([0], [1]))
    assert row["status"] == setups.STATUS_TARGET
    assert row["r_now"] is None  # a closed card is not marked to market
    assert abs(row["r_result"] - (2.0 - _COST_R)) < 1e-9
    assert row["bars_held"] == 2
    assert row["closed_utc"] is not None


def test_stop_wins_when_one_bar_touches_both_barriers() -> None:
    """D-012 holds here too: the conservative reading must not flip a loss into a win."""
    # The double-touch bar is second to last - closed at read time - and the trailing bar is the
    # forming candle the walk must never grade.
    bars = make_bars(
        [1.500, 1.502, 1.700, 1.700],
        [1.501, 1.503, 1.720, 1.705],
        [1.499, 1.501, 1.390, 1.695],
    )
    row = setups.latest_setup(bars, always([0], [1]))
    assert row["status"] == setups.STATUS_STOP
    assert abs(row["r_result"] - (-1.0 - _COST_R)) < 1e-9
    assert row["bars_held"] == 1


def test_a_signal_on_the_last_closed_bar_becomes_a_take_now_card() -> None:
    """A fresh signal's entry is the forming candle's open - the price a market order meets now.

    The signal itself came from the last CLOSED bar, so it is stable for the whole candle; the
    plan reads the live bar, and no verdict may arrive until that entry bar has closed.
    """
    # Three closed bars, then the forming candle trailing them.
    bars = make_bars([1.496, 1.492, 1.488, 1.494], [1.498, 1.494, 1.490, 1.500], [1.494, 1.490, 1.486, 1.492])
    # Fires on whatever history it is handed: on the closed view that is the last closed bar.
    def fn(view: Bars) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        fire = np.array([view.n - 1], dtype=np.int64)
        return fire, np.array([-1], dtype=np.int64), np.array([10.0]), np.array([2.0])

    row = setups.latest_setup(bars, {"id": "synthetic", "description": "", "fn": fn})
    assert row["status"] == setups.STATUS_OPEN
    # Short entry on the forming bar's open plus half the spread: 1.494 + 0.005.
    assert row["entry"] == 1.499
    assert row["sl"] == 1.599
    assert row["tp"] == 1.299
    # The card entered on the bar that is still forming: it is the market's newest, not yet walked.
    assert row["bars_held"] == 0
    assert row["closed_utc"] is None


def test_only_the_forming_bar_seeing_a_signal_leaves_no_card() -> None:
    """The forming candle cannot fire a rule on its own: that was the on/off card.

    Undercuts on the still-open candle look like a break until the candle finishes. The rules see
      closed bars only, so a spike that exists only inside the forming candle never becomes a
    card - it appears, if at all, once the bar has closed and the reading cannot change.
    """
    # Closed bars whose newest low is no fresh undercut (it stays above the earlier trough),
    # then a forming candle undercutting all of them by miles.
    bars = make_bars(
        [1.496, 1.492, 1.496, 1.494],
        [1.498, 1.494, 1.498, 1.500],
        [1.494, 1.490, 1.496, 1.200],
    )

    def fn(view: Bars) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        if view.n >= 2 and float(view.low[-1]) < float(view.low[:-1].min()):
            fire = np.array([view.n - 1], dtype=np.int64)
            return fire, np.array([1], dtype=np.int64), np.array([10.0]), np.array([2.0])
        empty = np.zeros(0, dtype=np.int64)
        return empty, np.zeros(0), np.zeros(0), np.zeros(0)

    family: Family = {"id": "synthetic", "description": "", "fn": fn}
    row = setups.latest_setup(bars, family)
    assert row["status"] == setups.STATUS_NONE
    # The same shape on a CLOSED bar is a real reading, not flicker: the undercut sits on the
    # last closed bar with a forming bar trailing it, and the card appears, planned at the
    # forming open.
    closed_undercut = make_bars(
        [1.496, 1.492, 1.486, 1.492],
        [1.498, 1.494, 1.488, 1.500],
        [1.494, 1.490, 1.486, 1.490],
    )
    row = setups.latest_setup(closed_undercut, family)
    assert row["status"] == setups.STATUS_OPEN
    assert row["bars_held"] == 0


def test_time_exit_uses_the_close_of_the_hold_limit() -> None:
    bars = make_bars(
        [1.500, 1.502, 1.505, 1.510, 1.520],
        [1.501, 1.503, 1.506, 1.511, 1.521],
        [1.499, 1.501, 1.504, 1.509, 1.519],
    )
    row = setups.latest_setup(bars, always([0], [1]), max_hold_bars=2)
    assert row["status"] == setups.STATUS_TIME
    assert row["bars_held"] == 2
    # Exit at the close two bars after entry: (1.510 - 1.502) / 0.10 = 0.08R.
    assert abs(row["r_result"] - (0.08 - _COST_R)) < 1e-9


def test_short_card_mirrors_the_long_one() -> None:
    bars = make_bars(
        [1.500, 1.498, 1.490, 1.480],
        [1.501, 1.499, 1.491, 1.481],
        [1.499, 1.497, 1.489, 1.479],
    )
    row = setups.latest_setup(bars, always([0], [-1]))
    assert row["status"] == setups.STATUS_OPEN
    assert row["side"] == "sell"
    # Shorts fill on the ask: the open plus half of the 1-pip spread.
    assert abs(row["entry"] - (1.498 + 0.005)) < 1e-9
    assert row["sl"] > row["entry"] > row["tp"]
    assert row["r_now"] is not None and row["r_now"] > 0


def test_no_signal_reports_none_rather_than_failing() -> None:
    bars = make_bars([1.500, 1.501, 1.502], [1.501, 1.502, 1.503], [1.499, 1.500, 1.501])
    row = setups.latest_setup(bars, always([], []))
    assert row["status"] == setups.STATUS_NONE
    assert row["note"] is not None
    assert row["entry"] is None and row["r_now"] is None


def test_a_broken_rule_is_reported_not_raised() -> None:
    bars = make_bars([1.500, 1.501, 1.502], [1.501, 1.502, 1.503], [1.499, 1.500, 1.501])
    row = setups.latest_setup(bars, {"id": "boom", "description": "raises", "fn": _boom})
    assert row["status"] == setups.STATUS_ERROR
    assert row["note"] is not None and "boom" in row["note"]


def test_a_refire_while_the_position_is_open_is_the_same_card() -> None:
    """D-035: the second signal is the first trade restated, not a new one to display.

    This is the "just entered" bug. The rule fires again on bar 3 while the bar-1 long is still
    running; showing the newest signal reset the clock to zero bars held every single bar, so a
    position that was hours deep read as freshly entered, at a loss the display had forgotten.
    """
    bars = make_bars(
        [1.500, 1.502, 1.520, 1.530, 1.540],
        [1.501, 1.503, 1.525, 1.535, 1.545],
        [1.499, 1.501, 1.515, 1.525, 1.535],
    )
    row = setups.latest_setup(bars, always([0, 3], [1, -1]))
    assert row["signal_epoch"] == int(bars.time[0])  # the trade as taken, not as restated
    assert row["side"] == "buy"
    assert row["status"] == setups.STATUS_OPEN
    assert row["bars_held"] == 3  # ... and the real age, not zero
    assert row["resignals"] == 1  # the bar-3 sell it stood aside for
    assert row["signals"] == 2
    assert row["taken"] == 1


def test_a_signal_after_the_position_closed_is_a_new_card() -> None:
    bars = make_bars(
        [1.500, 1.502, 1.400, 1.398, 1.396],
        [1.501, 1.503, 1.401, 1.399, 1.397],
        [1.499, 1.501, 1.395, 1.393, 1.391],  # bar 2 takes out the long's 1.402 stop
    )
    row = setups.latest_setup(bars, always([0, 3], [1, -1]))
    assert row["signal_epoch"] == int(bars.time[3])
    assert row["side"] == "sell"
    assert row["resignals"] == 0
    assert row["taken"] == 2


def test_build_puts_live_cards_first(monkeypatch: pytest.MonkeyPatch) -> None:
    bars = make_bars(
        [1.500, 1.502, 1.520, 1.530, 1.535],
        [1.501, 1.503, 1.525, 1.535, 1.540],
        [1.499, 1.501, 1.515, 1.525, 1.530],
    )
    # The long card never reaches its 2R target, so it stays live; the short card with a 2-pip
    # stop is taken out by the next bar's high, so it is closed. Live must sort above closed.
    live = always([0], [1])
    closed = always([0], [-1], stop_pips=2.0)
    monkeypatch.setattr(setups, "families_for", lambda timeframe: [closed, live])
    payload = setups.build(bars, "H1", symbol="USDJPY")
    assert [row["status"] for row in payload["setups"]] == [setups.STATUS_OPEN, setups.STATUS_STOP]
    assert payload["open_count"] == 1
    assert payload["timeframe"] == "H1"
    assert payload["digits"] == 3
    assert payload["asof_bar_utc"] == utc_str(int(bars.time[-1]), 0)
    assert STAMP.match(payload["asof_bar_utc"])
    assert payload["context"]["last_closed_utc"] == utc_str(int(bars.time[-2]), 0)


def test_utc_offset_moves_the_labels_but_not_the_age() -> None:
    bars = make_bars(
        [1.500, 1.502, 1.520, 1.530],
        [1.501, 1.503, 1.525, 1.535],
        [1.499, 1.501, 1.515, 1.525],
    )
    server = setups.latest_setup(bars, always([0], [1]), offset_s=0)
    utc = setups.latest_setup(bars, always([0], [1]), offset_s=3 * 3600)
    assert server["signal_utc"] != utc["signal_utc"]
    assert server["age_s"] == utc["age_s"]


def _wavy(n: int = 400) -> list[float]:
    return [1.500 + 0.010 * float(np.sin(i / 7.0)) for i in range(n)]


def test_context_reads_the_last_closed_bar_not_the_forming_one() -> None:
    closes = _wavy()
    bars = make_bars(closes, [c + 0.005 for c in closes], [c - 0.005 for c in closes])
    ctx = setups.market_context(bars, offset_s=3 * 3600)
    i = bars.n - 2
    # Every reading is anchored on the last CLOSED bar, statistics included - not on the end of
    # the array, which is the candle still being built.
    assert ctx["close"] == round(float(bars.close[i]), 3)
    assert ctx["last_price"] == round(float(bars.close[-1]), 3)
    assert ctx["last_closed_utc"] == utc_str(int(bars.time[i]), 3 * 3600)
    assert ctx["ema_fast"] == round(float(_ema(bars.close, 50)[i]), 3)
    assert ctx["ema_slow"] == round(float(_ema(bars.close, 200)[i]), 3)
    assert ctx["atr_pips"] == round(float(_atr(bars)[i]) / bars.pip(), 1)


def test_a_wild_forming_bar_cannot_move_the_context() -> None:
    """The rules cannot see a half-built candle, so neither may the backdrop they are read against."""
    closes = _wavy()
    bars = make_bars(closes, [c + 0.005 for c in closes], [c - 0.005 for c in closes])
    spiked = make_bars(
        closes[:-1] + [closes[-1] + 5.0],
        [c + 0.005 for c in closes[:-1]] + [closes[-1] + 5.2],
        [c - 0.005 for c in closes[:-1]] + [closes[-1] - 0.2],
    )
    before = setups.market_context(bars, offset_s=3 * 3600)
    after = setups.market_context(spiked, offset_s=3 * 3600)
    assert after["last_price"] == round(closes[-1] + 5.0, 3)
    for key in ("close", "ema_fast", "ema_slow", "ema_trend", "atr_pips", "range_pos", "fresh_high"):
        assert after[key] == before[key], key


def test_every_real_family_reports_a_consistent_row() -> None:
    """The invariants the page depends on, over all twelve rules on one synthetic series."""
    closes = [150.00 + 0.30 * np.sin(i / 11.0) + 0.004 * i for i in range(900)]
    bars = make_bars(
        closes,
        [c + 0.02 for c in closes],
        [c - 0.02 for c in closes],
    )
    payload = setups.build(bars, "M15", symbol="USDJPY", offset_s=3 * 3600)
    assert len(payload["setups"]) == len(families_for("M15"))
    assert payload["open_count"] == sum(
        1 for row in payload["setups"] if row["status"] == setups.STATUS_OPEN
    )
    for row in payload["setups"]:
        assert row["status"] in {
            setups.STATUS_OPEN,
            setups.STATUS_TARGET,
            setups.STATUS_STOP,
            setups.STATUS_TIME,
            setups.STATUS_NONE,
            setups.STATUS_ERROR,
        }
        if row["status"] in (setups.STATUS_OPEN, setups.STATUS_TARGET, setups.STATUS_STOP):
            assert row["side"] in ("buy", "sell")
            assert row["entry"] is not None and row["sl"] is not None and row["tp"] is not None
            assert row["stop_pips"] is not None and row["stop_pips"] > 0
        if row["status"] == setups.STATUS_OPEN:
            # A live card is marked to market and has no result yet; a closed one is the reverse.
            assert row["r_now"] is not None and row["r_result"] is None
            assert row["closed_utc"] is None
            if row["side"] == "buy":
                assert row["sl"] < row["entry"] < row["tp"]
            else:
                assert row["tp"] < row["entry"] < row["sl"]
        elif row["status"] in (setups.STATUS_TARGET, setups.STATUS_STOP, setups.STATUS_TIME):
            assert row["r_now"] is None and row["r_result"] is not None
            assert row["closed_utc"] is not None


def test_the_card_names_the_settings_the_rule_ran_with() -> None:
    """A card is read against a chart, and one family id can be several different rules.

    `ma_crossover` is 9/21 EMA, 20/50 SMA and 50/200 SMA in the sweep; the live view runs the
    registered default. A card that does not say which pair it used sends the reader to the wrong
    lines, which is exactly how a correct card comes to look like a wrong one - the pair the
    reader has on their chart crossed, this card's pair did not, and nothing on the row says so.
    """
    bars = make_bars([1.500, 1.502, 1.520, 1.530, 1.540], [1.501, 1.503, 1.525, 1.535, 1.545],
                     [1.499, 1.501, 1.515, 1.525, 1.535])
    family = next(f for f in families_for("H1") if f["id"] == "ma_crossover")
    row = setups.latest_setup(bars, family)
    assert row["params"] is not None

    # And the label has to be the rule's own defaults, or it is a second copy free to drift away
    # from the function it describes the moment someone re-tunes the rule.
    params = inspect.signature(ma_crossover).parameters
    assert row["params"] == f"{params['fast'].default}/{params['slow'].default} " \
        f"{str(params['kind'].default).upper()}"
    # A rule whose name already says what it does carries no label rather than an empty one.
    assert setups.latest_setup(bars, always([0], [1]))["params"] is None
