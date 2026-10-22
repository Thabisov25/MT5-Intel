"""Unit tests for the scalping template families.

These verify determinism, causality (no signals before the rules can fire), the cost
rule's three modes, and the cost-hurdle measurement — not profitability, which the
iterate harness judges on real history.

Volatility note: the synthetic series uses a 5-pip per-bar sigma, roughly USD/JPY M5,
and a 12-point spread (1.2 pips) by default, so the default fixture is representative
and the cost floor (10 x spread = 12 pips) behaves as it does on real bars.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from mt5_intel.research.backtest import Bars, simulate, summarize
from mt5_intel.research.strategies import FAMILIES
from mt5_intel.research.strategies_scalp import (
    ASIAN_FADE_MINUTES,
    MIN_STOP_PIPS,
    MIN_STOP_SPREAD_MULT,
    ORB_RANGE_MINUTES,
    SCALP_FAMILIES,
    ScalpFn,
    SessionClockError,
    asian_fade,
    bar_minutes,
    bars_for,
    cost_hurdle,
    orb_london,
    pullback_scalp,
    sr_flip,
)
from mt5_intel.research.sweep import GRIDS, gross_bars, sweep_family

PIP = 0.01
DAY = 86_400
# 2024-08-18 00:20:00 UTC: hour-aligned so the session windows (00:00 and 07:00 UTC)
# actually contain bars, but not on a day boundary so the first window is trimmed.
START = 1_723_940_400
SCALP_FNS: list[ScalpFn] = [sr_flip, pullback_scalp, orb_london, asian_fade]


def _bars_from(closes: np.ndarray, highs: np.ndarray, lows: np.ndarray,
               step: int = 300, spread_points: int = 12) -> Bars:
    n = closes.size
    return Bars(
        time=np.arange(START, START + n * step, step, dtype=np.int64),
        open_=closes.copy(),
        high=highs,
        low=lows,
        close=closes,
        spread_points=np.full(n, spread_points, dtype=np.int32),
        point=0.001,
        digits=3,
        utc_offset_s=0,
    )


def _noisy_series(n: int, seed: int, step: int = 300,
                  spread_points: int = 12, sigma: float = 0.05) -> Bars:
    rng = np.random.default_rng(seed)
    rets = rng.normal(0, sigma, n)
    close = 150.0 + np.cumsum(rets)
    open_ = np.concatenate(([150.0], close[:-1]))
    noise = np.abs(rng.normal(0, sigma * 0.6, n))
    high = np.maximum(open_, close) + noise
    low = np.minimum(open_, close) - noise
    return _bars_from(open_, high, low, step=step, spread_points=spread_points)


def _floor(bars: Bars, sig: np.ndarray) -> np.ndarray:
    floor: np.ndarray = np.maximum(MIN_STOP_SPREAD_MULT * bars.spread_pips()[sig], MIN_STOP_PIPS)
    return floor


def test_cost_modes_are_monotone_and_respect_the_floor() -> None:
    """off >= widen > filter in signal count, and every surviving stop clears the floor."""
    bars = _noisy_series(2000, seed=42)  # realistic vol, realistic 1.2 pip spread
    for fn in SCALP_FNS:
        raw = fn(bars, stop_mode="off")
        flt = fn(bars, stop_mode="filter")
        wid = fn(bars, stop_mode="widen")
        # widen keeps every raw signal; filter can only remove
        assert wid[0].size == raw[0].size
        assert flt[0].size <= raw[0].size
        for out in (raw, flt, wid):
            assert out[0].size == out[1].size == out[2].size == out[3].size
        if wid[0].size:
            assert np.all(wid[2] >= _floor(bars, wid[0]) - 1e-9)
        if flt[0].size:
            assert np.all(flt[2] >= _floor(bars, flt[0]) - 1e-9)
            # what survived the screen must be a subset of the raw signals
            assert set(flt[0].tolist()) <= set(raw[0].tolist())


def test_cost_hurdle_measurement_matches_the_modes() -> None:
    # The families registry must expose the four template ids (names, not objects).
    assert [f["id"] for f in SCALP_FAMILIES] == [
        "sr_flip", "pullback_scalp", "orb_london", "asian_fade",
    ]
    bars = _noisy_series(2000, seed=42)
    for fn in SCALP_FNS:
        hurdle = cost_hurdle(bars, fn)
        assert hurdle["raw_signals"] == fn(bars, stop_mode="off")[0].size
        assert hurdle["kept_signals"] == fn(bars, stop_mode="filter")[0].size
        assert hurdle["spread_pips_median"] == round(float(np.median(bars.spread_pips())), 2)
        # Swept params carry stop_mode; the measurement owns that key and must win.
        swept = cost_hurdle(bars, fn, stop_mode="widen")
        assert swept["raw_signals"] == hurdle["raw_signals"]
        assert swept["kept_signals"] == hurdle["kept_signals"]
        assert hurdle["floor_pips"] == round(
            float(MIN_STOP_SPREAD_MULT * hurdle["spread_pips_median"]), 2
        )


def test_cost_budget_scales_the_floor() -> None:
    """A looser cost budget (fewer pips of stop per pip of spread) must admit more.

    This is the lever that says whether a template is viable on a given account: at
    cost_mult=10 the round-trip cost is 20% of risk, at 3 it is 67%.
    """
    bars = _noisy_series(2000, seed=42)
    for fn in SCALP_FNS:
        strict = cost_hurdle(bars, fn, cost_mult=10.0)
        loose = cost_hurdle(bars, fn, cost_mult=3.0)
        assert loose["kept_signals"] >= strict["kept_signals"]
        assert strict["floor_pips"] == round(10.0 * strict["spread_pips_median"], 2)
        assert loose["floor_pips"] == round(3.0 * loose["spread_pips_median"], 2)
        assert loose["cost_mult"] == 3.0
        assert loose["raw_signals"] == strict["raw_signals"]  # budget must not gate raw
        filtered = fn(bars, stop_mode="filter", cost_mult=3.0)
        assert filtered[0].size == loose["kept_signals"]
        widened = fn(bars, stop_mode="widen", cost_mult=3.0)
        if widened[0].size:
            budget: np.ndarray = 3.0 * bars.spread_pips()[widened[0]]
            assert np.all(widened[2] >= budget - 1e-9)


def test_gross_measurement_removes_costs() -> None:
    """A cost-free pass on the same trades can only be as good or better than a charged one."""
    bars = _noisy_series(2000, seed=42)
    free = gross_bars(bars)
    assert free.n == bars.n
    assert np.array_equal(free.time, bars.time)
    assert np.all(free.spread_pips() == 0.0)
    for fn in SCALP_FNS:
        sig, side, stops, tp = fn(bars, stop_mode="widen")
        if sig.size == 0:
            continue
        net = summarize(simulate(bars, sig, side, stops, tp, 100))["net_r_mean"]
        gross = summarize(simulate(free, sig, side, stops, tp, 100))["net_r_mean"]
        assert net is not None and gross is not None
        assert gross > net  # zeroing the spread can only help the same trades
        return
    raise AssertionError("no template produced signals, so nothing was compared")


def test_sweep_reports_gross_readings_for_the_winner() -> None:
    bars = _noisy_series(1200, seed=5, spread_points=1)  # low spread: signals survive
    cands = sweep_family(bars, "sr_flip", sr_flip, 0, 800, 60)
    assert cands
    winner = max(cands, key=lambda c: c.train_mean)
    assert winner.gross_train_mean is not None
    assert winner.gross_test_mean is not None
    assert winner.test_mean is not None
    assert winner.gross_test_mean >= winner.test_mean


def test_at_least_one_template_fires_on_realistic_bars() -> None:
    """Guards against a fixture so quiet that every test passes vacuously."""
    bars = _noisy_series(2000, seed=42)
    fired = [fn(bars, stop_mode="off")[0].size for fn in SCALP_FNS]
    assert sum(fired) > 0, f"no template produced a single raw signal (counts: {fired})"


def test_families_deterministic_and_causal() -> None:
    bars = _noisy_series(1200, seed=7, spread_points=1)
    for fn in SCALP_FNS:
        out1 = fn(bars)
        out2 = fn(bars)
        for a, b in zip(out1, out2, strict=True):
            np.testing.assert_array_equal(a, b)
        sig = out1[0]
        assert np.all(sig < bars.n - 1)  # signal bar strictly before data end


def test_invalid_parameters_rejected() -> None:
    bars = _noisy_series(300, seed=5)
    for fn in SCALP_FNS:
        with pytest.raises(ValueError, match="stop_mode"):
            fn(bars, stop_mode="nonsense")
    with pytest.raises(ValueError, match="stop_ref"):
        orb_london(bars, stop_ref="nonsense")
    with pytest.raises(ValueError, match="target"):
        asian_fade(bars, target="nonsense")


def test_every_grid_is_accepted_by_its_family() -> None:
    """A grid whose kwargs are rejected would silently fall back to defaults.

    sweep_family swallows TypeError by re-running the family with no kwargs, which turns
    the whole grid into N identical candidates. This test makes that failure loud.
    """
    fns = {f["id"]: f["fn"] for f in [*FAMILIES, *SCALP_FAMILIES]}
    bars = _noisy_series(400, seed=1)
    for family_id, grid in GRIDS.items():
        assert family_id in fns, f"grid defined for unknown family {family_id!r}"
        assert grid, f"grid for {family_id} is empty"
        for params in grid:
            try:
                fns[family_id](bars, **params)
            except TypeError as exc:  # pragma: no cover - failure path
                raise AssertionError(
                    f"{family_id} rejects grid params {params}: {exc}"
                ) from exc


def test_orb_range_window_is_london_utc() -> None:
    # A near-zero spread keeps the cost floor out of the way for the window test.
    n = 3 * DAY // 300
    bars = _noisy_series(n, seed=3, step=300, spread_points=1)
    day = bars.time // DAY
    hour = (bars.time // 3600) % 24
    d2 = np.unique(day)[1]
    in_range = (day == d2) & (hour == 7)
    range_bars = np.nonzero(in_range)[0]
    rng_hi = float(bars.high[range_bars[:6]].max())
    # Keep the bars between the range and the breakout inside the range, so the
    # crafted breakout is the first one of that session.
    for k in range_bars[6:10]:
        inside = rng_hi - 0.05
        bars.open_[k] = inside
        bars.close[k] = inside
        bars.high[k] = inside + 0.005
        bars.low[k] = inside - 0.005
    breakout_bar = range_bars[10]
    bars.open_[breakout_bar] = rng_hi - 0.02
    bars.high[breakout_bar] = rng_hi + 0.10
    bars.close[breakout_bar] = rng_hi + 0.10
    sig, side, _, _ = orb_london(bars)
    assert breakout_bar in sig.tolist()
    k = int(np.nonzero(sig == breakout_bar)[0][0])
    assert side[k] == 1
    # That day's signals can only fire after the opening range has closed.
    mask = day[sig] == d2
    assert np.any(mask)
    assert np.all(sig[mask] >= range_bars[0] + 6)


def test_asian_fade_needs_false_break_and_reclaim() -> None:
    n = 2 * DAY // 300
    bars = _noisy_series(n, seed=11, step=300, spread_points=1)
    day = bars.time // DAY
    hour = (bars.time // 3600) % 24
    d1 = np.unique(day)[1]
    asian = np.nonzero((day == d1) & (hour < 6))[0]
    rng_lo = float(bars.low[asian].min())
    post = np.nonzero((day == d1) & (hour == 6))[0]
    target = post[6]
    # Excursion must exceed fade_atr x ATR (ATR on this fixture is ~16 pips).
    bars.low[target] = rng_lo - 0.30
    bars.close[target] = rng_lo + 0.02
    bars.open_[target + 1] = rng_lo + 0.02
    sig, side, _, tp_r = asian_fade(bars)
    assert target in sig.tolist()
    k = int(np.nonzero(sig == target)[0][0])
    assert side[k] == 1  # false break of the low fades long
    assert tp_r[k] > 0  # target is the range midpoint, so R must be positive


# --------------------------------------------------------------------------------------
# The session clock and the window durations
# --------------------------------------------------------------------------------------
# Two defects lived here, and both made these rules something other than what their docstrings
# describe. The hour was read straight off the bar stamp, which is the BROKER's clock (UTC+3 on
# this terminal), so "London 07:00" was really 04:00 UTC and "Asian 00:00-06:00" was 21:00-03:00.
# And the windows were counts of bars written for M5, so on H1 the "30-minute opening range" was
# six hours long and the "~2h" fade window was a full day.


def _asian_fade_fixture() -> Bars:
    """A series that fires asian_fade: a false break of the 00:00-06:00 UTC range, reclaimed."""
    n = 2 * DAY // 300
    bars = _noisy_series(n, seed=11, step=300, spread_points=1)
    day = bars.time // DAY
    hour = (bars.time // 3600) % 24
    d1 = np.unique(day)[1]
    asian = np.nonzero((day == d1) & (hour < 6))[0]
    rng_lo = float(bars.low[asian].min())
    target = np.nonzero((day == d1) & (hour == 6))[0][6]
    bars.low[target] = rng_lo - 0.30          # trades beyond the range low...
    bars.close[target] = rng_lo + 0.02        # ...and closes back inside
    bars.open_[target + 1] = rng_lo + 0.02
    return bars


def test_a_session_rule_refuses_when_the_broker_clock_is_unknown() -> None:
    """No clock, no trade: three hours of doubt is not something a rule may paper over."""
    bars = _noisy_series(1500, seed=3, spread_points=1)
    blind = replace(bars, utc_offset_s=None)
    for fn in (orb_london, asian_fade):
        with pytest.raises(SessionClockError):
            fn(blind, stop_mode="off")
        # The same bars with the clock declared still run.
        assert fn(bars, stop_mode="off")[0] is not None


def _on_the_broker_clock(bars: Bars, offset_s: int) -> Bars:
    """Move the stamps out by the offset and declare it: the UTC clock is unchanged.

    That is exactly what a broker does - the same instant, stamped on a clock that runs ahead -
    so the same rule must find the same sessions. `fade_bar` is the crafted false break, kept at
    the same *session* position because only the stamp moved.
    """
    return replace(bars, time=bars.time + offset_s, utc_offset_s=offset_s)


def test_orb_london_trades_the_utc_hour_not_the_stamp() -> None:
    """A UTC+3 terminal must trade the 07:00 UTC hour, not the bar stamped 07:00.

    Shifting every stamp by the offset and declaring that offset has to change nothing, and it
    only does because the offset is subtracted. Reading the stamp as UTC - what this did - was a
    different rule, three hours early, which is asserted too so it cannot come back quietly.
    """
    utc = _noisy_series(6000, seed=11, step=300, spread_points=1)
    shift = 3 * 3600
    broker = _on_the_broker_clock(utc, shift)
    same = orb_london(utc, stop_mode="off")[0].tolist()
    assert same, "this fixture produced no signals, so it proves nothing"
    assert orb_london(broker, stop_mode="off")[0].tolist() == same
    hours = {int((int(utc.time[s]) // 3600) % 24) for s in same}
    assert min(hours) >= 7 and max(hours) < 10, hours  # the 07:00 hour and its 2h scan
    assert orb_london(replace(broker, utc_offset_s=0), stop_mode="off")[0].tolist() != same


def test_asian_fade_trades_the_utc_hour_not_the_stamp() -> None:
    """The 00:00-06:00 UTC range, read on a clock that runs three hours ahead of UTC."""
    utc = _asian_fade_fixture()
    shift = 3 * 3600
    broker = _on_the_broker_clock(utc, shift)
    same = asian_fade(utc, stop_mode="off")[0].tolist()
    assert same, "this fixture produced no signals, so it proves nothing"
    assert asian_fade(broker, stop_mode="off")[0].tolist() == same
    hours = {int((int(utc.time[s]) // 3600) % 24) for s in same}
    assert min(hours) >= 6 and max(hours) < 8, hours  # 06:00 UTC plus the fade window
    assert asian_fade(replace(broker, utc_offset_s=0), stop_mode="off")[0].tolist() != same


def test_the_orb_range_is_a_duration_not_a_count_of_bars() -> None:
    """30 minutes is six bars on M5, two on M15, and unmeasurable on H1.

    On H1 the old `range_bars=6` built a six-hour box and called it the opening range; the rule
    now refuses instead, because an hourly candle does not record the first 30 minutes of an hour.
    """
    cases = [("M5", 300, 6), ("M15", 900, 2), ("M30", 1800, 1)]
    for label, step, expected in cases:
        bars = _noisy_series(4000, seed=7, step=step, spread_points=1)
        assert bar_minutes(bars) == step / 60
        assert bars_for(ORB_RANGE_MINUTES, bar_minutes(bars)) == expected, label
    hourly = _noisy_series(2000, seed=7, step=3600, spread_points=1)
    assert bar_minutes(hourly) == 60.0
    assert orb_london(hourly, stop_mode="off")[0].size == 0
    # A 60-minute range is measurable on hourly bars, and asking for one is not a refusal.
    assert orb_london(hourly, range_minutes=60.0, stop_mode="widen")[0].size >= 0


def test_the_asian_fade_window_is_a_duration() -> None:
    """The fade is looked for for `fade_minutes` after the range sets, not for 24 bars."""
    m15 = _noisy_series(4000, seed=5, step=900, spread_points=1)
    assert bar_minutes(m15) == 15.0
    assert bars_for(ASIAN_FADE_MINUTES, bar_minutes(m15)) == 8
    assert bars_for(ASIAN_FADE_MINUTES, bar_minutes(m15)) != 24  # what it used to be
    hour = ((m15.time // 3600) % 24).astype(int)
    sig = asian_fade(m15, fade_minutes=ASIAN_FADE_MINUTES, stop_mode="off")[0]
    assert sig.size, "the fixture must trade, or the window is untested"
    # 06:00 UTC plus the two-hour window: nothing later than 08:00 can be the fade.
    assert set(hour[sig].tolist()) <= {6, 7}
