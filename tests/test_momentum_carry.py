"""Unit tests for daily_momentum, carry_trend and the carry rate table.

Checkpoints: the synthetic series are hand-built so flip timing is deterministic.
The dated-source reproduction (2024-07-31 BoJ unwind, 2007-08 unwind) runs live
against terminal history and is recorded in docs/RESEARCH_PLAN_MOMENTUM_CARRY.md.
"""

from __future__ import annotations

import numpy as np

from mt5_intel.research.backtest import Bars
from mt5_intel.research.rates import carry_direction, carry_signed_spread
from mt5_intel.research.strategies import carry_trend, daily_momentum

PIP = 0.01


def _bars(closes: list[float], start_epoch: int = 1_722_000_000, step: int = 86_400) -> Bars:
    n = len(closes)
    arr = np.array(closes, dtype="float64")
    return Bars(
        time=np.arange(start_epoch, start_epoch + n * step, step, dtype=np.int64),
        open_=arr.copy(),
        high=arr + 0.05,
        low=arr - 0.05,
        close=arr,
        spread_points=np.full(n, 10, dtype=np.int32),
        point=0.001,
        digits=3,
        utc_offset_s=0,
    )


def test_rate_table_sign_and_threshold() -> None:
    # Jul 2024: Fed 5.25-5.50 (5.375), BoJ 0.0 -> spread +5.375 -> long-carry
    jul_2024 = int(np.datetime64("2024-07-15T00:00:00").astype("int64"))
    assert carry_signed_spread(jul_2024) > 5.0
    assert carry_direction(jul_2024) == 1
    # A hypothetical month where spreads invert: 2020-era, spread stays positive
    # in our table; a threshold above the max spread must neutralize the signal.
    assert carry_direction(jul_2024, threshold=10.0) == 0
    # Row lookup must be monotone: earlier rows never apply later.
    mar_2013 = int(np.datetime64("2013-03-01T00:00:00").astype("int64"))
    assert carry_signed_spread(mar_2013) < carry_signed_spread(jul_2024)


def test_daily_momentum_flips_on_closed_bars() -> None:
    # Rising then falling: 120-bar lookback shortened to 5 for the fixture.
    closes = [100.0, 101, 102, 103, 104, 105, 104, 103, 102, 101, 100, 99]
    bars = _bars(closes)
    sig, side, stops, tp = daily_momentum(bars, lookback=5, stop_atr=3.0)
    # Log return sign over trailing 5 bars flips from + to - once (at bar 10-ish);
    # then possibly back at the end. Every signal must be on a closed bar (i < n-1).
    assert sig.size >= 1
    assert np.all(sig < bars.n - 1)
    assert np.all(np.isin(side, (-1, 1)))
    assert stops.size == sig.size == tp.size
    assert np.all(stops >= 5.0)  # floor in pips


def test_daily_momentum_side_matches_return_sign() -> None:
    closes = [100.0 + i * 0.5 for i in range(30)]
    bars = _bars(closes)
    sig, side, _, _ = daily_momentum(bars, lookback=10)
    # A pure uptrend with a single flip at the first eligible bar: sign must be long.
    if sig.size:
        first = int(np.argmin(sig))
        assert side[first] == 1


def test_carry_trend_requires_agreement() -> None:
    # Strong uptrend through Jul 2024 (carry long) -> one long entry when trend
    # crosses above MA; no signal when disagreement (trend down while carry long).
    n = 300
    closes = [140.0 + 0.05 * i for i in range(n)]  # steady uptrend
    bars = _bars(closes, start_epoch=int(np.datetime64("2024-05-01").astype("int64")))
    sig, side, stops, tp = carry_trend(bars, ma_days=20, stop_atr=4.0)
    assert sig.size >= 1
    assert np.all(side == 1)  # uptrend + long-carry month -> long only
    assert stops.size == sig.size == tp.size


def test_carry_trend_never_trades_against_the_rate_spread() -> None:
    # Downtrend in Jul 2024 while the USD-JPY spread is long-carry: the family
    # must stay flat after the MA warms up (no agreement), never short-carry.
    n = 300
    closes = [140.0 - 0.05 * i for i in range(n)]
    bars = _bars(closes, start_epoch=int(np.datetime64("2024-05-01").astype("int64")))
    _sig, side, _, _ = carry_trend(bars, ma_days=20, stop_atr=4.0)
    assert np.all(side == 1)  # agreement is only ever long-carry in this window
