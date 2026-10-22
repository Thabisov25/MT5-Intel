"""Unit tests for the regime harness and the extended (1973+) carry rate table.

Two things would silently invalidate a regime claim, so both are pinned here: the sign the
rate table assigns to a given era (the 1975-1977 window where the BoJ discount rate sat *above*
fed funds turns the modern carry trade on its head), and the half-open window arithmetic that
decides which trades belong to which regime.
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import pairwise

import numpy as np

from mt5_intel.research.backtest import Bars
from mt5_intel.research.rates import carry_direction, carry_signed_spread
from mt5_intel.research.regimes import (
    MIN_TRADES,
    REGIMES,
    _bar_bounds,
    _carry_signs,
    _params_label,
    grade_window,
)


def _epoch(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp())


def _daily_bars(start: str, days: int, close: float = 150.0) -> Bars:
    t0 = _epoch(start)
    time = t0 + np.arange(days, dtype=np.int64) * 86_400
    arr = np.full(days, close, dtype="float64")
    return Bars(
        time=time,
        open_=arr.copy(),
        high=arr + 0.5,
        low=arr - 0.5,
        close=arr,
        spread_points=np.full(days, 10, dtype=np.int32),
        point=0.001,
        digits=3,
        utc_offset_s=0,
    )


def test_regimes_are_contiguous_and_ordered() -> None:
    for earlier, later in pairwise(REGIMES):
        assert earlier.end == later.start, f"{earlier.label} does not meet {later.label}"
    for regime in REGIMES:
        assert regime.start < regime.end
        assert regime.label and regime.note


def test_regime_coverage_starts_where_the_rate_table_has_a_verified_row() -> None:
    # The first row added from primary sources is 1973-04; earlier data is excluded on purpose.
    assert REGIMES[0].start == "1973-04-01"
    assert REGIMES[-1].end > "2026-10-01"


def test_carry_sign_is_negative_in_1975_1977_and_positive_after_1977() -> None:
    # The BoJ official discount rate exceeded the fed funds rate through most of 1975-1977,
    # so the carry leg asks for the *opposite* side of USDJPY there than it does today.
    assert carry_signed_spread(_epoch("1975-06-16")) < 0
    assert carry_direction(_epoch("1975-06-16")) == -1
    assert carry_direction(_epoch("1976-06-16")) == -1
    assert carry_direction(_epoch("1977-04-16")) == -1  # last negative row
    assert carry_direction(_epoch("1977-09-16")) == 1   # Fed back above the BoJ
    assert carry_direction(_epoch("2024-07-16")) == 1
    assert carry_signed_spread(_epoch("2024-07-16")) > 5.0


def test_carry_sign_is_zero_when_the_two_rates_are_level() -> None:
    # The 1991-12 row is Fed 4.50 / BoJ 4.50 -> no carry either way.
    assert carry_direction(_epoch("1992-06-16")) == 0
    assert carry_signed_spread(_epoch("1992-06-16")) == 0.0


def test_carry_sign_legend_reports_a_flip_and_a_constant() -> None:
    bars = _daily_bars("1973-04-01", 2000)  # runs to 1978-09
    lo, hi = _bar_bounds(bars, "1973-04-01", "1979-01-01")
    assert _carry_signs(bars, lo, hi) == "+-"  # positive then negative: the rule flips sides
    lo2, hi2 = _bar_bounds(bars, "1980-01-01", "1985-01-01")
    assert _carry_signs(bars, lo2, hi2) == "-"  # window is outside the data, and that is stated


def test_bar_bounds_are_half_open() -> None:
    bars = _daily_bars("2024-01-01", 400)
    lo, hi = _bar_bounds(bars, "2024-01-01", "2024-03-01")
    assert int(bars.time[lo]) == _epoch("2024-01-01")  # start is included
    assert int(bars.time[hi]) == _epoch("2024-03-01")  # end is excluded
    assert hi > lo


def test_grade_window_only_counts_signals_inside_the_window() -> None:
    bars = _daily_bars("2024-01-01", 400)
    sig_i = np.array([10, 50, 80, 200], dtype=np.int64)
    side = np.ones(4, dtype=np.int64)
    stops = np.full(4, 20.0)
    tp_r = np.full(4, 2.0)
    res = grade_window(bars, sig_i, side, stops, tp_r, 40, 100, 30, seed=11)
    assert res["n"] == 2  # bars 50 and 80 only
    assert set(res) == {
        "n",
        "net_r_mean",
        "gross_r_mean",
        "placebo_r_mean",
        "stressed_r_mean",
        "lower_ci_r",
        "win_rate",
        "passes_gates",
    }


def test_low_trade_count_cannot_pass_the_gates() -> None:
    bars = _daily_bars("2024-01-01", 400)
    sig_i = np.array([10, 20], dtype=np.int64)  # far below MIN_TRADES
    res = grade_window(
        bars, sig_i, np.ones(2, dtype=np.int64), np.full(2, 20.0), np.full(2, 2.0), 0, 100, 30, 11
    )
    assert res["n"] == 2 < MIN_TRADES
    assert res["passes_gates"] is False


def test_empty_window_is_reported_not_crashed() -> None:
    bars = _daily_bars("2024-01-01", 100)
    res = grade_window(
        bars,
        np.array([5], dtype=np.int64),
        np.ones(1, dtype=np.int64),
        np.full(1, 20.0),
        np.full(1, 2.0),
        60,
        80,
        30,
        11,
    )
    assert res["n"] == 0
    assert res["net_r_mean"] is None
    assert res["passes_gates"] is False


def test_variant_labels_are_readable_and_stable() -> None:
    assert _params_label({}) == "defaults"
    assert _params_label({"ma_days": 200, "stop_atr": 3.0}) == "ma_days=200 stop_atr=3.0"
