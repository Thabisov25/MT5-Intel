"""Pure data-quality helpers. Times are epoch seconds, UTC unless a function says otherwise."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np

HALF_HOUR_S = 1800
HOUR_S = 3600

# Fast-move circuit breaker: the newest closed bar is judged against the average true range
# of the bars just before it. A range this much larger than normal is a spike (news), not
# noise, so the gate should not issue anything into it.
FAST_MOVE_ATR_BARS = 20
FAST_MOVE_ATR_MULT = 3.0


@dataclass(frozen=True)
class Gap:
    start_epoch: int
    end_epoch: int
    weekend_closure: bool

    @property
    def missing_s(self) -> int:
        return self.end_epoch - self.start_epoch


@dataclass(frozen=True)
class HourStat:
    hour: int
    samples: int
    median: float | None
    p90: float | None


@dataclass(frozen=True)
class FastMove:
    """How the newest closed bar compares with recent volatility (values in price units)."""

    measured: bool
    tripped: bool
    ratio: float | None
    atr: float | None
    last_range: float | None
    bars: int


def is_weekend_closure(start_epoch: int, end_epoch: int) -> bool:
    """Heuristic: a gap that starts Friday 20:00 UTC or later and ends Sunday or Monday."""
    start = datetime.fromtimestamp(start_epoch, tz=UTC)
    end = datetime.fromtimestamp(end_epoch, tz=UTC)
    return (
        start.weekday() == 4
        and start.hour >= 20
        and end.weekday() in (6, 0)
        and end_epoch - start_epoch <= 60 * HOUR_S
    )


def find_gaps(times: np.ndarray, interval_s: int, tolerance: float = 1.5) -> list[Gap]:
    values = np.asarray(times, dtype=np.int64)
    if values.size < 2:
        return []
    diffs = np.diff(values)
    if np.any(diffs < 0):
        raise ValueError("bar times must be ascending")
    gaps: list[Gap] = []
    for i in np.flatnonzero(diffs > interval_s * tolerance):
        start, end = int(values[i]), int(values[i + 1])
        gaps.append(Gap(start, end, is_weekend_closure(start, end)))
    return gaps


def is_market_open_utc(now_epoch: int) -> bool:
    """Approximate FX hours: closed from Friday 21:00 UTC to Sunday 21:00 UTC. Holidays not modelled."""
    now = datetime.fromtimestamp(now_epoch, tz=UTC)
    closed = (
        now.weekday() == 5
        or (now.weekday() == 4 and now.hour >= 21)
        or (now.weekday() == 6 and now.hour < 21)
    )
    return not closed


def estimate_server_offset_s(tick_server_epoch: int, now_utc_epoch: int) -> int:
    """Round (server clock - UTC) to the nearest half hour. Valid only for a live, fresh tick."""
    raw = tick_server_epoch - now_utc_epoch
    return round(raw / HALF_HOUR_S) * HALF_HOUR_S


def offset_is_plausible(offset_s: int) -> bool:
    return offset_s % HOUR_S == 0 and -12 * HOUR_S <= offset_s <= 14 * HOUR_S


def spread_by_hour(times_utc: np.ndarray, spread_pips: np.ndarray) -> list[HourStat]:
    hours = (np.asarray(times_utc, dtype=np.int64) % 86_400) // HOUR_S
    spreads = np.asarray(spread_pips, dtype=np.float64)
    stats: list[HourStat] = []
    for hour in range(24):
        sample = spreads[hours == hour]
        if sample.size == 0:
            stats.append(HourStat(hour, 0, None, None))
        else:
            stats.append(
                HourStat(
                    hour,
                    int(sample.size),
                    float(np.median(sample)),
                    float(np.percentile(sample, 90)),
                )
            )
    return stats


def fast_move(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    *,
    bars: int = FAST_MOVE_ATR_BARS,
    mult: float = FAST_MOVE_ATR_MULT,
) -> FastMove:
    """Trip when the newest closed bar's true range is `mult` times the recent average.

    The arrays must hold closed bars, oldest first, with the newest judged bar last: a bar that
    is still forming would be graded on a range that has not finished. True range includes the
    previous close, so a gap counts as movement rather than as a small bar.

    "Unmeasurable" comes back as tripped, never as calm: the gate is default-deny, so not being
    able to measure a fast move must not read as safety.
    """
    high = np.asarray(highs, dtype="float64")
    low = np.asarray(lows, dtype="float64")
    close = np.asarray(closes, dtype="float64")
    size = min(high.size, low.size, close.size)
    # One bar supplies the previous close; the next `bars` are the baseline; one is judged.
    if size < bars + 2:
        return FastMove(measured=False, tripped=True, ratio=None, atr=None, last_range=None, bars=0)
    high, low, close = high[-size:], low[-size:], close[-size:]
    prev_close = close[:-1]
    true_range = np.maximum.reduce(
        [high[1:] - low[1:], np.abs(high[1:] - prev_close), np.abs(low[1:] - prev_close)]
    )
    window = true_range[-(bars + 1) : -1]
    atr = float(np.mean(window))
    last = float(true_range[-1])
    bars_used = int(window.size)
    if not np.isfinite(atr) or atr <= 0:
        # A dead-flat series has no volatility to compare against.
        return FastMove(measured=False, tripped=True, ratio=None, atr=None, last_range=last, bars=bars_used)
    return FastMove(
        measured=True,
        tripped=bool(last / atr > mult),
        ratio=last / atr,
        atr=atr,
        last_range=last,
        bars=bars_used,
    )
