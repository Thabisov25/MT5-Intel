from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from mt5_intel.datachecks import (
    FAST_MOVE_ATR_BARS,
    estimate_server_offset_s,
    fast_move,
    find_gaps,
    is_market_open_utc,
    is_weekend_closure,
    offset_is_plausible,
    spread_by_hour,
)


def _epoch(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp())


def test_weekend_closure_is_classified_and_mid_week_gap_is_not() -> None:
    friday_close = _epoch(2026, 10, 2, 21, 0)
    sunday_open = _epoch(2026, 10, 4, 22, 0)
    assert is_weekend_closure(friday_close, sunday_open)
    assert not is_weekend_closure(_epoch(2026, 10, 7, 10, 0), _epoch(2026, 10, 7, 12, 0))


def test_find_gaps_flags_weekend_closure_as_expected() -> None:
    weekend = np.array([_epoch(2026, 10, 2, 20, 59), _epoch(2026, 10, 4, 22, 0)], dtype=np.int64)
    gaps = find_gaps(weekend, 60)
    assert len(gaps) == 1
    assert gaps[0].weekend_closure


def test_find_gaps_flags_mid_week_outage_as_unexpected() -> None:
    minute = 60
    wed_start = _epoch(2026, 10, 7, 0, 0)
    times = list(range(wed_start, wed_start + 60 * minute, minute))
    times += list(range(wed_start + 62 * minute, wed_start + 120 * minute, minute))  # 2-min hole
    gaps = find_gaps(np.array(times, dtype=np.int64), minute)
    assert len(gaps) == 1
    assert not gaps[0].weekend_closure


def test_find_gaps_rejects_descending_times() -> None:
    with pytest.raises(ValueError):
        find_gaps(np.array([3, 2, 1], dtype=np.int64), 60)


def test_market_hours_rule() -> None:
    assert is_market_open_utc(_epoch(2026, 10, 7, 10, 0))  # Wednesday
    assert not is_market_open_utc(_epoch(2026, 10, 10, 10, 0))  # Saturday
    assert not is_market_open_utc(_epoch(2026, 10, 9, 21, 30))  # Friday after close
    assert not is_market_open_utc(_epoch(2026, 10, 4, 20, 0))  # Sunday before open
    assert is_market_open_utc(_epoch(2026, 10, 4, 22, 0))  # Sunday after open


def test_offset_estimate_from_live_tick_rounds_to_half_hour() -> None:
    now = _epoch(2026, 10, 7, 10, 0)
    tick_server = now + 3 * 3600 - 2  # GMT+3 with a 2 s old tick
    assert estimate_server_offset_s(tick_server, now) == 3 * 3600
    assert offset_is_plausible(3 * 3600)
    assert not offset_is_plausible(3 * 3600 + 900)


def test_spread_by_hour_reports_median_and_p90() -> None:
    base = _epoch(2026, 10, 5, 8, 0)
    times = np.array([base + i * 300 for i in range(6)], dtype=np.int64)  # all in hour 8 UTC
    spreads = np.array([1.0, 1.2, 1.4, 1.6, 1.8, 2.0])
    stats = {s.hour: s for s in spread_by_hour(times, spreads)}
    assert stats[8].samples == 6
    assert stats[8].median == pytest.approx(1.5)
    assert stats[8].p90 == pytest.approx(1.9, rel=1e-3)
    assert stats[9].samples == 0 and stats[9].median is None


def _bars(ranges: list[float]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Bars whose true ranges are exactly `ranges`: each bar opens at the previous close."""
    moves = np.asarray(ranges, dtype="float64")
    closes = np.cumsum(moves)
    opens = closes - moves
    return np.maximum(opens, closes), np.minimum(opens, closes), closes


def test_fast_move_is_calm_when_the_last_bar_is_ordinary() -> None:
    highs, lows, closes = _bars([0.010] * (FAST_MOVE_ATR_BARS + 2))
    move = fast_move(highs, lows, closes)
    assert move.measured is True
    assert move.tripped is False
    assert move.ratio == pytest.approx(1.0)
    assert move.atr == pytest.approx(0.010)
    assert move.bars == FAST_MOVE_ATR_BARS


def test_fast_move_trips_on_a_spike_nowhere_near_the_average() -> None:
    highs, lows, closes = _bars([0.010] * (FAST_MOVE_ATR_BARS + 1) + [0.050])
    move = fast_move(highs, lows, closes)
    assert move.tripped is True
    assert move.ratio == pytest.approx(5.0)
    assert move.last_range == pytest.approx(0.050)


def test_fast_move_respects_its_multiplier_boundary() -> None:
    calm = _bars([0.010] * (FAST_MOVE_ATR_BARS + 1) + [0.029])
    assert fast_move(*calm, mult=3.0).tripped is False
    at_threshold = _bars([0.010] * (FAST_MOVE_ATR_BARS + 1) + [0.030])
    assert fast_move(*at_threshold, mult=3.0).tripped is False  # strictly greater than trips
    tighter = _bars([0.010] * (FAST_MOVE_ATR_BARS + 1) + [0.025])
    assert fast_move(*tighter, mult=2.0).tripped is True


def test_fast_move_counts_a_gap_as_movement() -> None:
    # Same tiny bar size throughout, but the last bar opens far from the previous close.
    highs = np.array([1.001] * (FAST_MOVE_ATR_BARS + 1) + [1.041])
    lows = np.array([1.000] * (FAST_MOVE_ATR_BARS + 1) + [1.040])
    closes = np.array([1.0005] * (FAST_MOVE_ATR_BARS + 1) + [1.0405])
    move = fast_move(highs, lows, closes)
    assert move.tripped is True
    # True range is the gap to the previous close (1.0405 - 1.0005), not the 1-pip bar height.
    assert move.last_range == pytest.approx(0.0405, abs=1e-9)


def test_fast_move_reports_unmeasurable_as_tripped() -> None:
    """Too few bars, or a dead-flat series, must never read as calm."""
    short = _bars([0.010] * (FAST_MOVE_ATR_BARS + 1))
    move = fast_move(*short)
    assert move.measured is False and move.tripped is True and move.bars == 0

    flat = fast_move(*_bars([0.0] * (FAST_MOVE_ATR_BARS + 2)))
    assert flat.measured is False and flat.tripped is True and flat.atr is None

    empty = fast_move(np.zeros(0), np.zeros(0), np.zeros(0))
    assert empty.measured is False and empty.tripped is True
