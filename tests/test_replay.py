"""Tests for the replay card generator with hand-built synthetic bars."""

from __future__ import annotations

from itertools import pairwise

import numpy as np

from mt5_intel.research.backtest import (
    CLOSED_SL,
    CLOSED_TIME,
    CLOSED_TP,
    Bars,
    one_at_a_time,
    plan_trade,
    resolve_outcome,
    simulate,
    walk_trade,
)
from mt5_intel.research.replay import build_cards, sample_cards, summarize_cards
from mt5_intel.research.strategies import Family as ReplayFamily

PIP = 0.01  # 3-digit symbol: point = 0.001, pip = 0.01


def test_sample_cards_caps_each_family() -> None:
    """A flat cap would hide whichever families trade least; the cap must be per family."""
    cards = [{"family": "busy"} for _ in range(50)] + [{"family": "rare"} for _ in range(3)]
    out = sample_cards(cards, per_family=2)
    assert [c["family"] for c in out] == ["busy", "busy", "rare", "rare"]
    assert sample_cards([], per_family=2) == []


def make_bars(closes: list[float], highs: list[float], lows: list[float]) -> Bars:
    """Noisy but monotone synthetic series so plans and grades are deterministic.

    Opens equal closes each bar; between-bar moves are 0.02 so the price path is
    readable while a 10-pip (0.10) stop and 20-pip TP stay achievable in-scenario.
    """
    n = len(closes)
    return Bars(
        time=np.arange(1_700_000_000, 1_700_000_000 + n * 900, 900, dtype=np.int64),
        open_=np.array(closes),
        high=np.array(highs),
        low=np.array(lows),
        close=np.array(closes),
        spread_points=np.full(n, 10, dtype=np.int32),
        point=0.001,
        digits=3,
        utc_offset_s=0,
    )


def _long_setup() -> Bars:
    # Entry at bar 1 open (1.502); TP = +0.20 above entry = 1.702.
    return make_bars(
        [1.500, 1.502, 1.520, 1.700],
        [1.501, 1.503, 1.535, 1.710],
        [1.499, 1.501, 1.515, 1.690],
    )


def test_plan_trade_long_prices() -> None:
    bars = _long_setup()
    plan = plan_trade(bars, 0, 1, 1, stop_pips=10.0, tp_r=2.0)
    assert plan is not None
    assert plan["entry"] == 1.502
    # 10 pips = 10 * 0.01 = 0.10 stop distance.
    assert plan["sl"] == 1.402
    assert plan["tp"] == 1.702


def test_plan_trade_short_uses_ask_and_mirrors() -> None:
    bars = make_bars(
        [1.500, 1.498],
        [1.501, 1.499],
        [1.499, 1.497],
    )
    plan = plan_trade(bars, 0, 1, -1, stop_pips=10.0, tp_r=2.0)
    assert plan is not None
    # spread_points=10 -> 1 pip spread; ask = open + half spread = 1.498 + 0.005
    assert abs(plan["entry"] - 1.503) < 1e-9
    assert abs(plan["sl"] - 1.603) < 1e-9
    assert abs(plan["tp"] - 1.303) < 1e-9


def test_resolve_outcome_tp_first_wins() -> None:
    bars = make_bars(
        [1.500, 1.502, 1.520, 1.530],
        [1.501, 1.503, 1.710, 1.531],
        [1.499, 1.501, 1.515, 1.525],
    )
    plan = plan_trade(bars, 0, 1, 1, stop_pips=10.0, tp_r=2.0)
    assert plan is not None
    outcome = resolve_outcome(bars, plan, 0, 1, max_hold_bars=10)
    assert outcome is not None
    assert outcome.touched_tp
    assert not outcome.touched_sl
    assert outcome.bars_held == 2  # hit TP on the third bar (index 2)
    assert outcome.r > 0
    # TP distance = 2R, so a clean TP pays net_r ≈ 2 minus costs.
    assert abs(outcome.r - (2.0 - plan["cost_r"])) < 1e-9


def test_resolve_outcome_sl_first_when_both_touched_in_one_bar() -> None:
    bars = make_bars(
        [1.500, 1.502, 1.700],
        [1.501, 1.503, 1.720],
        [1.499, 1.501, 1.390],
    )
    plan = plan_trade(bars, 0, 1, 1, stop_pips=10.0, tp_r=2.0)
    assert plan is not None
    outcome = resolve_outcome(bars, plan, 0, 1, max_hold_bars=10)
    assert outcome is not None
    # D-012: conservative — SL fires first when one bar touches both barriers.
    assert outcome.touched_sl
    assert not outcome.touched_tp
    assert outcome.r == -1.0 - plan["cost_r"]


def test_build_cards_and_summary() -> None:
    bars = make_bars(
        [1.500, 1.502, 1.520, 1.700],
        [1.501, 1.503, 1.525, 1.710],
        [1.499, 1.501, 1.515, 1.690],
    )
    family: ReplayFamily = {
        "id": "synthetic_long",
        "description": "test family: always-buy on bar 0",
        "fn": lambda b: (
            np.array([0], dtype=np.int64),
            np.array([1], dtype=np.int64),
            np.full(1, 10.0),
            np.full(1, 2.0),
        ),
    }
    cards = build_cards(bars, family, max_hold_bars=10)
    assert len(cards) == 1
    card = cards[0]
    assert card["side"] == "buy"
    assert card["entry"] == "1.502"
    assert card["sl"] == "1.402"
    assert card["tp"] == "1.702"
    assert card["signal_utc"].startswith("2023-11-14")
    assert card["grade"]["hit"] == "TP"
    summ = summarize_cards(cards, "synthetic_long")
    assert summ["n"] == 1
    assert summ["hit_tp"] == 1
    assert summ["net_r_mean"] == summ["net_sum_r"]


# --- D-035: one position at a time -----------------------------------------------------------------

def _ramp(n: int) -> Bars:
    """A gently rising series: no barrier is reached, so every trade is a time exit."""
    closes = [1.500 + 0.0002 * i for i in range(n)]
    return make_bars(closes, [c + 0.0005 for c in closes], [c - 0.0005 for c in closes])


def _always(bars: Bars, *, stop_pips: float = 5.0) -> tuple[np.ndarray, ...]:
    """Signals on every bar, so only the flat-only rule can decide what gets traded."""
    sig = np.arange(0, bars.n - 2, dtype=np.int64)
    return (
        sig,
        np.ones(sig.size, dtype=np.int64),
        np.full(sig.size, stop_pips),
        np.full(sig.size, 2.0),
    )


def test_one_at_a_time_takes_the_first_signal_of_a_run() -> None:
    bars = _ramp(6)
    sig, side, stops, tps = _always(bars)
    taken = one_at_a_time(bars, sig, side, stops, tps, max_hold_bars=2)
    # Signal 0 enters on bar 1 and time-exits on bar 3, so nothing before bar 4 is a new trade.
    # The bar-4 entry has no full window left, so it is the position still running.
    assert [k for k, _, _ in taken] == [0, 3]
    assert taken[0] == (0, 3, CLOSED_TIME)
    assert taken[1] == (3, None, "open")


def test_one_at_a_time_reopens_only_after_the_previous_trade_closed() -> None:
    bars = _ramp(20)
    sig, side, stops, tps = _always(bars)
    taken = one_at_a_time(bars, sig, side, stops, tps, max_hold_bars=4)
    entries = [int(sig[k]) + 1 for k, _, _ in taken]
    # Every later entry is strictly after the bar the previous trade ended on.
    for (_, exit_bar, _), nxt in zip(taken, entries[1:]):
        assert exit_bar is not None
        assert nxt > exit_bar
    assert len(taken) < sig.size / 3


def test_walk_trade_agrees_with_the_graded_simulation() -> None:
    """walk_trade is the one answer to "where does this trade end".

    The live view, the flat-only filter and the replay's own decision all read it. If it ever
    drifted from the grader the page would report a position the backtest does not believe in,
    so the two are pinned together here over a noisy series and three hold limits.
    """
    rng = np.random.default_rng(7)
    closes = 150 + np.cumsum(rng.normal(0, 0.12, 200))
    bars = make_bars(closes.tolist(), (closes + 0.09).tolist(), (closes - 0.09).tolist())
    for max_hold in (2, 3, 10):
        # Stop short of the tail: there simulate grades a trade walk_trade still calls open.
        sig = np.arange(0, bars.n - 3 - max_hold, dtype=np.int64)
        side = np.where(np.arange(sig.size) % 2 == 0, 1, -1).astype(np.int64)
        stops = np.full(sig.size, 8.0)
        tps = np.full(sig.size, 2.0)
        accepted = one_at_a_time(bars, sig, side, stops, tps, max_hold)
        outcomes = simulate(bars, sig, side, stops, tps, max_hold)
        assert len(outcomes) == len(accepted)
        for (k, exit_bar, kind), outcome in zip(accepted, outcomes):
            assert kind != "open"
            assert exit_bar is not None
            assert outcome.bars_held == exit_bar - (int(sig[k]) + 1)
            assert outcome.touched_sl == (kind == CLOSED_SL)
            assert outcome.touched_tp == (kind == CLOSED_TP)


def test_walk_trade_reports_an_unfinished_window_as_open() -> None:
    """The one case the grader cannot express: a trade that has neither closed nor run out of time."""
    bars = _ramp(5)
    plan = plan_trade(bars, 0, 1, 1, stop_pips=5.0, tp_r=2.0)
    assert plan is not None
    assert walk_trade(bars, 1, 1, plan["sl"], plan["tp"], 10) == (None, "open")
    assert walk_trade(bars, 1, 1, plan["sl"], plan["tp"], 2) == (3, CLOSED_TIME)


def test_one_at_a_time_decides_in_time_order_whatever_order_it_is_given() -> None:
    """The placebo controls pass the same bars with a randomised side; the filter must not care."""
    bars = _ramp(20)
    sig, side, stops, tps = _always(bars)
    straight = one_at_a_time(bars, sig, side, stops, tps, max_hold_bars=4)
    shuffled = np.random.default_rng(1).permutation(sig.size)
    perm_sig, perm_side = sig[shuffled], side[shuffled]
    scrambled = one_at_a_time(bars, perm_sig, perm_side, stops[shuffled], tps[shuffled], 4)
    # Same signals traded, judged by the bar they fired on rather than by array position.
    assert sorted(int(sig[k]) for k, _, _ in straight) == sorted(
        int(perm_sig[k]) for k, _, _ in scrambled
    )


def test_build_cards_grades_one_position_once() -> None:
    """A rule that re-fires every bar produced a card per bar before this; it must not now."""
    bars = _ramp(120)
    sig, side, stops, tps = _always(bars)

    def fn(_bars: Bars) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        return sig, side, stops, tps

    family: ReplayFamily = {"id": "always", "description": "fires every bar", "fn": fn}
    cards = build_cards(bars, family, max_hold_bars=5)
    taken = one_at_a_time(bars, sig, side, stops, tps, max_hold_bars=5)
    assert len(cards) == len(taken)
    assert len(cards) < sig.size / 3
    # No card's entry falls inside the previous card's holding window.
    epochs = [c["entry_epoch"] for c in cards]
    gaps = [b - a for a, b in pairwise(epochs)]
    assert all(gap >= (5 + 1) * 900 for gap in gaps)
