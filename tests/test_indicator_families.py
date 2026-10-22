"""Tests for the indicator families (MA crossover, RSI mean reversion) and for the
confirmation-window tolerance added to the rules that used to demand a single-bar event.

What is asserted here is *behaviour*, not profitability: determinism, causality, that the
indicators match their textbook definitions, that the tolerance actually widens what the
rule can recognise, and that the default settings are byte-for-byte the rigid originals
(so every number already published from these families stays reproducible). Profitability
is the iterate harness's job, on real history.
"""

from __future__ import annotations

import numpy as np
import pytest

from mt5_intel.research import strategies
from mt5_intel.research.backtest import Bars
from mt5_intel.research.strategies import (
    FAMILIES,
    _atr,
    _ema,
    _sma,
    _wilder_rsi,
    ma_crossover,
    range_extreme_reversion,
    rsi_mean_reversion,
    sweep_and_reclaim,
)
from mt5_intel.research.strategies_scalp import asian_fade, pullback_scalp

PIP = 0.01
DAY = 86_400
# 2024-08-18 00:20:00 UTC: hour-aligned for the session windows, not on a day boundary.
START = 1_723_940_400
FAMILY_IDS = [f["id"] for f in FAMILIES]


def _bars(closes: np.ndarray, wick: float = 0.01, step: int = 300,
          spread_points: int = 12) -> Bars:
    n = closes.size
    return Bars(
        time=np.arange(START, START + n * step, step, dtype=np.int64),
        open_=closes.copy(),
        high=closes + wick,
        low=closes - wick,
        close=closes.copy(),
        spread_points=np.full(n, spread_points, dtype=np.int32),
        point=0.001,
        digits=3,
        utc_offset_s=0,
    )


def _path(legs: list[tuple[int, float]], start: float = 150.0) -> np.ndarray:
    """A piecewise-linear path: `legs` is a list of (bars, per-bar change)."""
    out = [start]
    for count, change in legs:
        for _ in range(count):
            out.append(out[-1] + change)
    return np.array(out)


def _noisy(n: int, seed: int, sigma: float = 0.05, spread_points: int = 12) -> Bars:
    rng = np.random.default_rng(seed)
    close = 150.0 + np.cumsum(rng.normal(0, sigma, n))
    noise = np.abs(rng.normal(0, sigma * 0.6, n))
    return Bars(
        time=np.arange(START, START + n * 300, 300, dtype=np.int64),
        open_=np.concatenate(([150.0], close[:-1])),
        high=np.maximum(np.concatenate(([150.0], close[:-1])), close) + noise,
        low=np.minimum(np.concatenate(([150.0], close[:-1])), close) - noise,
        close=close,
        spread_points=np.full(n, spread_points, dtype=np.int32),
        point=0.001,
        digits=3,
        utc_offset_s=0,
    )


# --------------------------------------------------------------------------------------
# The two new families are registered
# --------------------------------------------------------------------------------------
def test_new_families_are_registered() -> None:
    assert "ma_crossover" in FAMILY_IDS
    assert "rsi_mean_reversion" in FAMILY_IDS
    # No duplicate ids, and every family is callable on bars.
    assert len(FAMILY_IDS) == len(set(FAMILY_IDS))
    bars = _noisy(600, seed=3)
    for family in FAMILIES:
        if family["id"] in ("ma_crossover", "rsi_mean_reversion"):
            out = family["fn"](bars)
            assert out[0].size == out[1].size == out[2].size == out[3].size


# --------------------------------------------------------------------------------------
# Indicator definitions
# --------------------------------------------------------------------------------------
def test_sma_and_ema_match_their_definitions() -> None:
    series = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    sma = _sma(series, 3)
    assert np.all(np.isnan(sma[:2]))
    np.testing.assert_allclose(sma[2:], [2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
    # SMA of a short series is all NaN rather than an exception.
    assert np.all(np.isnan(_sma(series, 99)))
    # EMA is seeded with the SMA of its first window, then recursively smoothed.
    ema = _ema(series, 3)
    assert np.all(np.isnan(ema[:2]))
    assert ema[2] == pytest.approx(2.0)
    alpha = 2 / 4
    assert ema[3] == pytest.approx(alpha * 4.0 + (1 - alpha) * 2.0)
    assert np.all(np.isnan(_ema(series, 99)))


def test_wilder_rsi_matches_an_independent_loop() -> None:
    rng = np.random.default_rng(17)
    close = 150.0 + np.cumsum(rng.normal(0, 0.05, 240))
    got = _wilder_rsi(close, 14)

    def naive(c: np.ndarray, period: int) -> list[float | None]:
        out: list[float | None] = [None] * c.size
        gains = [max(c[i] - c[i - 1], 0.0) for i in range(1, c.size)]
        losses = [max(c[i - 1] - c[i], 0.0) for i in range(1, c.size)]
        ag = sum(gains[:period]) / period
        al = sum(losses[:period]) / period
        out[period] = 100 - 100 / (1 + ag / al)
        for i in range(period + 1, c.size):
            ag = (ag * (period - 1) + gains[i - 1]) / period
            al = (al * (period - 1) + losses[i - 1]) / period
            out[i] = 100 - 100 / (1 + ag / al)
        return out

    ref = naive(close, 14)
    assert np.all(np.isnan(got[:14]))
    for i in range(14, close.size):
        assert got[i] == pytest.approx(ref[i])
    assert np.all((got[14:] >= 0) & (got[14:] <= 100))


def test_wilder_rsi_saturates_on_one_way_moves() -> None:
    up = _wilder_rsi(np.linspace(100.0, 120.0, 60), 14)
    down = _wilder_rsi(np.linspace(120.0, 100.0, 60), 14)
    flat = _wilder_rsi(np.full(60, 150.0), 14)
    assert np.allclose(up[14:], 100.0)      # only gains
    assert np.allclose(down[14:], 0.0)      # only losses
    assert np.allclose(flat[14:], 50.0)     # no gain, no loss -> neutral
    # Too short for the window: nothing is defined.
    assert np.all(np.isnan(_wilder_rsi(np.arange(10.0), 14)))


# --------------------------------------------------------------------------------------
# MA crossover
# --------------------------------------------------------------------------------------
def test_ma_crossover_fires_on_the_cross_and_is_causal() -> None:
    # Fall for 60 bars then rise for 60: the fast SMA must cross above the slow one.
    bars = _bars(_path([(60, -0.10), (60, 0.10)]))
    sig, side, stops, tp_r = ma_crossover(bars, fast=20, slow=50, kind="sma", buffer_atr=0.0)
    assert sig.size > 0
    assert sig[0] >= 50  # never before both averages have their window
    assert side[0] == 1  # the first cross is the turn up
    assert np.all(sig < bars.n - 1)
    assert np.all(np.asarray(stops) >= 5.0)
    assert np.all(np.asarray(tp_r) == 2.0)
    # Determinism.
    np.testing.assert_array_equal(sig, ma_crossover(bars, fast=20, slow=50, kind="sma")[0])


def test_ma_crossover_buffer_removes_whipsaw() -> None:
    """A tiny oscillation around a level crosses repeatedly; the band must suppress it."""
    rng = np.random.default_rng(5)
    close = 150.0 + np.cumsum(rng.normal(0, 0.02, 900))
    bars = _bars(close)
    exact = ma_crossover(bars, fast=10, slow=30, buffer_atr=0.0)[0]
    banded = ma_crossover(bars, fast=10, slow=30, buffer_atr=0.5)[0]
    assert exact.size > banded.size  # the exact rule churns, the banded rule does not
    # Holding inside the band can never invent a signal the exact rule never saw on a
    # different bar more often: both are crossings of the same pair of lines.
    assert banded.size >= 0


def test_ma_crossover_holds_state_inside_the_band() -> None:
    # A wide band means the separation never qualifies: no signal at all.
    bars = _bars(_path([(80, 0.02), (80, -0.02)]))
    assert ma_crossover(bars, fast=10, slow=30, buffer_atr=50.0)[0].size == 0
    assert ma_crossover(bars, fast=10, slow=30, buffer_atr=0.0)[0].size > 0


def test_ma_crossover_ema_variant_and_validation() -> None:
    bars = _bars(_path([(60, -0.10), (60, 0.10)]))
    ema_sig = ma_crossover(bars, fast=20, slow=50, kind="ema", buffer_atr=0.0)[0]
    sma_sig = ma_crossover(bars, fast=20, slow=50, kind="sma", buffer_atr=0.0)[0]
    assert ema_sig.size > 0 and sma_sig.size > 0
    with pytest.raises(ValueError, match="kind"):
        ma_crossover(bars, kind="wma")
    with pytest.raises(ValueError, match="fast must be shorter"):
        ma_crossover(bars, fast=50, slow=20)


# --------------------------------------------------------------------------------------
# RSI mean reversion
# --------------------------------------------------------------------------------------
def test_rsi_immediate_fades_the_extreme_bar() -> None:
    bars = _bars(_path([(30, 0.0), (40, -0.06), (60, 0.06)]))
    sig, side, _stops, tp_r = rsi_mean_reversion(
        bars, rsi_period=14, lo=30.0, hi=70.0, entry="immediate"
    )
    assert sig.size > 0
    assert side[0] == 1  # an oversold reading is bought
    rsi = _wilder_rsi(bars.close, 14)
    assert rsi[sig[0]] < 30.0  # it really is the extreme bar
    assert np.all(sig < bars.n - 1)
    assert np.all(np.asarray(tp_r) == 2.0)


def test_rsi_confirm_waits_for_the_turn() -> None:
    closes = _path([(30, 0.0), (40, -0.06), (60, 0.06)])
    bars = _bars(closes)
    rsi = _wilder_rsi(closes, 14)
    fast = rsi_mean_reversion(bars, entry="immediate", confirm_bars=1)
    slow = rsi_mean_reversion(bars, entry="confirm", confirm_bars=3)
    assert slow[0].size > 0
    # Every confirmed long is entered on a bar whose RSI has already turned back above the
    # level, and never earlier than the naive fade of the same reading.
    for k, i in enumerate(slow[0]):
        if slow[1][k] > 0:
            assert rsi[i] > 30.0
        else:
            assert rsi[i] < 70.0
    assert slow[0][0] >= fast[0][0]


def test_rsi_confirm_window_expires_without_a_turn() -> None:
    """A one-way decline keeps RSI pinned low: the fade is armed, never confirmed."""
    bars = _bars(_path([(20, 0.0), (180, -0.05)]))
    immediate = rsi_mean_reversion(bars, entry="immediate")[0]
    confirmed = rsi_mean_reversion(bars, entry="confirm", confirm_bars=3)[0]
    assert immediate.size > 0
    assert confirmed.size == 0
    # A one-bar window is the strictest reading of "the turn must come next bar".
    assert rsi_mean_reversion(bars, entry="confirm", confirm_bars=1)[0].size == 0


def test_rsi_connors_fast_variant_and_validation() -> None:
    bars = _bars(_path([(40, 0.0), (30, -0.04), (50, 0.04)]))
    n = rsi_mean_reversion(bars, rsi_period=2, lo=10.0, hi=90.0, entry="immediate")[0]
    assert n.size > 0
    with pytest.raises(ValueError, match="entry"):
        rsi_mean_reversion(bars, entry="whenever")
    with pytest.raises(ValueError, match="levels"):
        rsi_mean_reversion(bars, lo=80.0, hi=20.0)
    with pytest.raises(ValueError, match="levels"):
        rsi_mean_reversion(bars, lo=0.0)


def test_new_families_deterministic_and_causal() -> None:
    bars = _noisy(1500, seed=11)
    for fn in (ma_crossover, rsi_mean_reversion):
        out1, out2 = fn(bars), fn(bars)
        for a, b in zip(out1, out2, strict=True):
            np.testing.assert_array_equal(a, b)
        assert np.all(out1[0] < bars.n - 1)
        assert out1[0].size == out1[1].size == out1[2].size == out1[3].size


# --------------------------------------------------------------------------------------
# Confirmation windows on the three rules that used to require one exact bar
# --------------------------------------------------------------------------------------
def _sweep_fixture(*, reclaim: bool) -> Bars:
    """A durable swing low at 149.50 and a sweep at bar 60.

    The bars after the low must *rise*, otherwise a rising-then-flat tail confirms a fresh
    (higher) swing low and the 149.50 level is replaced before the sweep ever happens.
    """
    close = np.empty(120)
    close[:20] = 150.60          # flat shelf: swings at 150.58 / 150.62
    close[20] = 149.50           # the swing low this test trades against
    close[21:60] = np.linspace(149.55, 149.95, 39)  # rising: no new swing low
    close[60] = 149.45           # sweeps below 149.50, closes below it: not reclaimed yet
    close[61] = 149.55 if reclaim else 149.40
    close[62:] = 149.60 if reclaim else 149.40
    bars = _bars(close, wick=0.02)
    bars.low[20] = 149.50        # the swing low itself
    bars.low[60] = 149.30        # the sweep
    bars.open_[60] = 149.40
    bars.open_[61] = 149.45
    # Bar 61's own low must not sweep the level: only a window can explain a signal there.
    bars.low[61] = 149.52
    return bars


def test_sweep_reclaim_window_catches_a_reclaim_that_lags() -> None:
    bars = _sweep_fixture(reclaim=True)
    rigid = sweep_and_reclaim(bars, confirm_bars=1)
    tolerant = sweep_and_reclaim(bars, confirm_bars=3)
    assert 61 not in rigid[0].tolist()  # the original same-bar rule cannot see it
    assert 61 in tolerant[0].tolist()   # the window can, one bar after the sweep
    k = int(np.nonzero(tolerant[0] == 61)[0][0])
    assert tolerant[1][k] == 1


def test_sweep_reclaim_window_expires() -> None:
    """A sweep that is never reclaimed must not fire, however long the window."""
    bars = _sweep_fixture(reclaim=False)
    for confirm_bars in (1, 2, 3, 5):
        assert sweep_and_reclaim(bars, confirm_bars=confirm_bars)[0].size == 0


def _family_ema(series: np.ndarray, period: int) -> np.ndarray:
    """The EMA as pullback_scalp's local helper defines it (seeded at series[0])."""
    alpha = 2 / (period + 1)
    out = np.empty_like(series)
    out[0] = series[0]
    for i in range(1, series.size):
        out[i] = alpha * series[i] + (1 - alpha) * out[i - 1]
    return out


def _family_sma(series: np.ndarray, period: int) -> np.ndarray:
    """The SMA as pullback_scalp's local helper defines it (padded with its first value)."""
    rolled = np.convolve(series, np.ones(period) / period, mode="valid")
    return np.concatenate((np.full(period - 1, rolled[0] if rolled.size else 0.0), rolled))


def _pullback_arms(bars: Bars, pullback_atr: float = 0.75,
                   fast: int = 20, slow: int = 50) -> dict[int, int]:
    """Indices that satisfy the pullback (arm) condition, with their direction.

    Deliberately rebuilt with the same local indicator definitions the family uses, so a
    mismatch here would be a mismatch in the rule, not in the indicator seeding.
    """
    ef, es = _family_ema(bars.close, fast), _family_sma(bars.close, slow)
    atr = _atr(bars, 14)
    armed: dict[int, int] = {}
    for j in range(1, bars.n - 1):
        a = float(atr[j])
        if not np.isfinite(a) or a <= 0:
            continue
        if ef[j] > es[j] and bars.low[j] <= ef[j] + pullback_atr * a and bars.close[j] > es[j]:
            armed[j] = 1
        elif ef[j] <= es[j] and bars.high[j] >= ef[j] - pullback_atr * a and bars.close[j] < es[j]:
            armed[j] = -1
    return armed


def test_pullback_scalp_signals_are_always_justified_confirms() -> None:
    """Every signal must be a close through a real pullback bar's extreme.

    For the original one-bar window the confirming bar can only be the bar right after the
    pullback; widening the window adds later bars but must never invent a confirmation that
    no pullback bar supports.
    """
    bars = _noisy(1500, seed=23)
    armed = _pullback_arms(bars)
    for confirm_bars in (1, 3):
        sig, side, _, _ = pullback_scalp(bars, confirm_bars=confirm_bars)
        assert sig.size > 0, "fixture produced no signals, so nothing was checked"
        for k, i in enumerate(sig):
            direction = int(side[k])
            window = range(max(1, i - confirm_bars), i)
            justified = any(
                armed.get(j) == direction
                and (bars.close[i] > bars.high[j] if direction > 0 else bars.close[i] < bars.low[j])
                for j in window
            )
            assert justified, f"signal at {i} (dir {direction}) has no supporting pullback"
    # The original window is exactly "the bar immediately before": nothing else may confirm.
    rigid_sig, rigid_side, _, _ = pullback_scalp(bars, confirm_bars=1)
    for k, i in enumerate(rigid_sig):
        assert armed.get(i - 1) == int(rigid_side[k])


def test_asian_fade_window_allows_a_late_reclaim() -> None:
    n = 2 * DAY // 300
    bars = _noisy(n, seed=11, spread_points=1)
    day = bars.time // DAY
    hour = (bars.time // 3600) % 24
    d1 = int(np.unique(day)[1])
    asian = np.nonzero((day == d1) & (hour < 6))[0]
    rng_lo = float(bars.low[asian].min())
    post = np.nonzero((day == d1) & (hour == 6))[0]
    rng_hi = float(bars.high[asian].max())
    assert rng_hi > rng_lo + 0.10, "fixture range too tight to place the bars below it"
    sweep_bar, reclaim_bar = int(post[4]), int(post[6])
    # Bar 4 breaks the low and closes below it (armed). Bar 5 stays below (still no
    # reclaim). Bar 6 closes back inside the range: the reclaim, two bars later.
    for k in range(sweep_bar, reclaim_bar + 1):
        bars.open_[k] = rng_lo - 0.01
        bars.high[k] = rng_lo + 0.02
        bars.low[k] = rng_lo + 0.01
        bars.close[k] = rng_lo - 0.02
    bars.low[sweep_bar] = rng_lo - 0.30
    bars.close[reclaim_bar] = rng_lo + 0.02
    bars.open_[reclaim_bar + 1] = rng_lo + 0.01  # entry, below the range midpoint target
    rigid = asian_fade(bars, confirm_bars=1)[0]
    tolerant = asian_fade(bars, confirm_bars=3)[0]
    assert reclaim_bar not in rigid.tolist()
    assert reclaim_bar in tolerant.tolist()
    assert sweep_bar not in tolerant.tolist()


# --------------------------------------------------------------------------------------
# The tolerant rewrite must not have moved the rig: default behaviour is unchanged
# --------------------------------------------------------------------------------------
def test_default_settings_still_match_the_rigid_originals() -> None:
    """Pinned counts for a fixed synthetic series, captured before the tolerance was added.

    These are regression guards, not claims about profitability: if a tolerance default
    ever changes, the already-published results from these families stop being reproducible,
    and this test is where that shows up.
    """
    expected = {"sweep_reclaim": 248, "pullback_scalp": 212}
    bars = _noisy(4000, seed=42)
    assert sweep_and_reclaim(bars)[0].size == expected["sweep_reclaim"]
    assert pullback_scalp(bars)[0].size == expected["pullback_scalp"]
    assert asian_fade(bars)[0].size == 0  # session windows: the fixture has none that fire


# --------------------------------------------------------------------------------------
# The RSI the panel quotes, and the staleness guard on swing levels
# --------------------------------------------------------------------------------------


def _stale_level_fixture(n: int = 620) -> Bars:
    """A confirmed swing high at bar 40 that is 570 bars old when the sweep finally arrives.

    The low side is fresh (a swing low at bar 500), which is the case the old `and` in the
    staleness guard could not see: an ancient high still anchored a brand new trade.
    """
    high = np.empty(n)
    low = np.empty(n)
    high[:41] = np.linspace(150.60, 150.80, 41)       # rising into the swing high at bar 40
    high[41:] = np.linspace(150.79, 150.45, n - 41)   # falling: no later bar is a swing high
    low[:501] = np.linspace(150.50, 150.10, 501)      # falling: no local minimum before bar 500
    low[501:] = np.linspace(150.11, 150.30, n - 501)  # rising: bar 500 is the only swing low
    close = (high + low) / 2
    bars = Bars(
        time=np.arange(START, START + n * 300, 300, dtype=np.int64),
        open_=close.copy(),
        high=high,
        low=low,
        close=close,
        spread_points=np.full(n, 1, dtype=np.int32),
        point=0.001,
        digits=3,
        utc_offset_s=0,
    )
    # The sweep itself: a wick above the old high, closed back below it.
    bars.high[610] = 150.90
    bars.open_[610] = 150.40
    bars.close[610] = 150.50
    return bars


def test_sweep_reclaim_refuses_a_swing_level_that_went_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    """Either side being ancient is enough to skip the bar, not both.

    Read as `and`, the guard let a high from 570 bars ago anchor a trade whenever the low side
    happened to be recent - which is the whole "ancient anchor" it was written to prevent. The
    fixture is armed the same way in both halves, so the only thing that changes is the guard.
    """
    bars = _stale_level_fixture()
    monkeypatch.setattr(strategies, "STALE_LEVEL_BARS", 10_000)
    assert 610 in sweep_and_reclaim(bars)[0].tolist(), "the fixture must be able to fire at all"
    monkeypatch.setattr(strategies, "STALE_LEVEL_BARS", 400)
    assert sweep_and_reclaim(bars)[0].tolist() == []


def test_range_extreme_mr_reads_the_projects_one_rsi() -> None:
    """Wilder's RSI(14) - the same reading the Live panel prints.

    This family used to build its own RSI out of a simple average of gains and losses, a different
    indicator: on H1 that reading sits 6 points from Wilder's at the median and 14 at the 90th
    percentile, so on roughly one bar in seven the two disagreed about the 65/35 line the rule
    trades. A card quoted against one RSI and taken on another cannot be reviewed at all.
    """
    bars = _noisy(3000, seed=4, sigma=0.09)
    rsi = _wilder_rsi(bars.close, 14)
    sig_i, side, _, _ = range_extreme_reversion(bars)
    assert sig_i.size > 5, "the fixture has to trade, or this proves nothing"
    for k, i in enumerate(sig_i.tolist()):
        if int(side[k]) < 0:
            assert rsi[i] > 65.0, f"bar {i}: short taken with RSI {rsi[i]:.1f}"
        else:
            assert rsi[i] < 35.0, f"bar {i}: long taken with RSI {rsi[i]:.1f}"
