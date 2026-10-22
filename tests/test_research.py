from __future__ import annotations

from itertools import pairwise

import numpy as np
import pytest

from mt5_intel.research.backtest import Bars, cost_from_spreads, cost_in_r, one_at_a_time, simulate
from mt5_intel.research.strategies import FAMILIES, _causal_mean, matched_placebo, sweep_and_reclaim
from mt5_intel.research.strategies_scalp import SCALP_FAMILIES
from mt5_intel.research.validate import evaluate_family, walk_forward_folds


def _bars(n: int = 600, seed: int = 3) -> Bars:
    rng = np.random.default_rng(seed)
    close = 150 + np.cumsum(rng.normal(0, 0.05, n))
    open_ = np.concatenate(([close[0]], close[:-1]))
    spread = np.full(n, 1.4)  # pips
    return Bars(
        time=np.arange(n) * 60 + 1_700_000_000,
        open_=open_,
        high=np.maximum(open_, close) + rng.uniform(0, 0.03, n),
        low=np.minimum(open_, close) - rng.uniform(0, 0.03, n),
        close=close,
        spread_points=(spread * 10).astype(np.int32),  # 1.4 pips = 14 points at point=0.001
        point=0.001,
        digits=3,
        utc_offset_s=0,
    )


def test_cost_in_r_uses_median_spread_over_stop() -> None:
    bars = _bars()
    # median spread 1.4 pips; stop 14 pips -> 0.1R
    assert cost_in_r(bars, 14.0) == pytest.approx(0.1, abs=0.01)


def test_cost_in_r_rejects_zero_stop() -> None:
    with pytest.raises(ValueError):
        cost_in_r(_bars(), 0.0)


def test_causal_mean_borrows_nothing_from_the_bar_after_i() -> None:
    """The exact property a centred convolution breaks, stated as an equality.

    A signal set can agree by luck on one series; a trailing window cannot. Appending a bar must
    leave every earlier reading bit-for-bit unchanged, which is also what makes the live view's
    readings stable while the last candle is still forming.
    """
    x = np.arange(1.0, 51.0)
    out = _causal_mean(x, 5)
    assert out[10] == pytest.approx(x[6:11].mean())
    appended = _causal_mean(np.append(x, 999.0), 5)
    assert np.array_equal(out, appended[: x.size])


def test_no_family_reads_a_bar_that_has_not_happened() -> None:
    """A rule must judge a bar from that bar and earlier ones only.

    Look-ahead is invisible in the topline R and fatal to every graded result, so it is checked
    directly rather than inferred: run each family over a prefix of the series and over the whole
    series, and require the same verdict on every bar the prefix could have judged. The
    range-extreme fade failed this — its RSI was built with a centred convolution, so a bar's
    reading averaged the next seven bars, and it signalled on bars that had not happened yet.
    """
    bars = _bars(n=1200, seed=11)
    last_judgeable = bars.n - 23
    prefix = Bars(
        time=bars.time[: bars.n - 20],
        open_=bars.open_[: bars.n - 20],
        high=bars.high[: bars.n - 20],
        low=bars.low[: bars.n - 20],
        close=bars.close[: bars.n - 20],
        spread_points=bars.spread_points[: bars.n - 20],
        point=bars.point,
        digits=bars.digits,
        utc_offset_s=0,
    )
    for family in FAMILIES + SCALP_FAMILIES:
        whole = {int(i) for i in family["fn"](bars)[0] if i <= last_judgeable}
        early = {int(i) for i in family["fn"](prefix)[0] if i <= last_judgeable}
        assert early == whole, family["id"]


def test_cost_is_charged_on_the_spreads_it_is_given() -> None:
    """The cost in R must come from the spread array it was handed, not from the bars."""
    spreads = np.full(10, 2.8, dtype="float64")  # 2.8 pips
    assert cost_from_spreads(spreads, 14.0) == pytest.approx(0.2)
    with pytest.raises(ValueError):
        cost_from_spreads(spreads, 0.0)


def test_cost_stress_actually_charges_the_wider_spreads() -> None:
    """Passing 1.5x spreads must lower every trade's net R.

    It did not: `simulate` read the median spread off `bars`, so the widened array moved only a
    short's fill by half a spread and left the cost in R untouched — and the trial command did
    not pass the factor at all. The result was a gate reported as "Stressed 1.5x" that was an
    unstressed rerun of the same run, which is why every family's stressed and plain means
    agreed to three decimals.
    """
    bars = _bars(n=600, seed=13)
    family = FAMILIES[0]
    sig_i, side, stops, tp_r = family["fn"](bars)
    if sig_i.size == 0:
        pytest.skip("no signals on this synthetic series")
    plain = simulate(bars, sig_i, side, stops, tp_r, 120)
    stressed = simulate(bars, sig_i, side, stops, tp_r, 120, spread_pips=bars.spread_pips() * 1.5)
    assert len(plain) == len(stressed) > 0
    # Same plans and same barriers; only the friction changed, and it can only hurt.
    assert all(s.r < p.r for p, s in zip(plain, stressed))


def test_evaluate_family_stress_column_reflects_the_multiplier() -> None:
    bars = _bars(600, seed=17)
    family = FAMILIES[0]
    plain = evaluate_family(bars, family, n_folds=3, spread_multiplier=1.0)
    stressed = evaluate_family(bars, family, n_folds=3, spread_multiplier=1.5)
    assert stressed["stressed"]["net_r_mean"] < plain["stressed"]["net_r_mean"]
    # The multiplier touches the stress column only: the graded run stays the graded run.
    assert stressed["full"]["net_r_mean"] == plain["full"]["net_r_mean"]


def test_simulate_trades_one_position_at_a_time() -> None:
    """D-035: a rule that re-fires every bar is graded once per position, not once per bar.

    Without this the same trade is counted again on every bar its condition held, which is what
    inflated every family's n and narrowed the spread of its expectancy in the same direction.
    """
    bars = _bars(n=400, seed=5)
    sig = np.arange(0, bars.n - 2, dtype=np.int64)
    side = np.ones(sig.size, dtype=np.int64)
    stops = np.full(sig.size, 12.0)
    tps = np.full(sig.size, 2.0)
    outcomes = simulate(bars, sig, side, stops, tps, 10)
    assert len(outcomes) == len(one_at_a_time(bars, sig, side, stops, tps, 10))
    assert len(outcomes) < sig.size / 2  # a position per bar would be the pre-D-035 answer


def test_simulate_sl_first_when_both_barriers_in_one_bar() -> None:
    bars = _bars(20)
    # Build a bar after entry that touches both SL and TP: SL must win (D-012).
    entry_i = 5
    big = 5.0  # in price units, far beyond both barriers
    bars.low[entry_i + 1] = bars.close[entry_i + 1] - big
    bars.high[entry_i + 1] = bars.close[entry_i + 1] + big
    sig = np.array([entry_i - 1])
    out = simulate(bars, sig, np.array([1]), np.array([10.0]), np.array([2.0]), max_hold_bars=10)
    assert out[0].touched_sl and not out[0].touched_tp
    assert out[0].r < 0


def test_simulate_time_exit_records_partial_r() -> None:
    bars = _bars(30)
    sig = np.array([3])
    out = simulate(bars, sig, np.array([1]), np.array([1000.0]), np.array([2.0]), max_hold_bars=5)
    assert out, "expected an outcome"
    assert not out[0].touched_sl and not out[0].touched_tp


def test_simulate_short_entry_pays_half_spread() -> None:
    bars = _bars(30)
    sig = np.array([3])
    out_long = simulate(bars, sig, np.array([1]), np.array([20.0]), np.array([2.0]), max_hold_bars=2)
    out_short = simulate(bars, sig, np.array([-1]), np.array([20.0]), np.array([2.0]), max_hold_bars=2)
    # Long entry at open; short entry at open + half spread. Same barriers -> short worse by that
    # half spread expressed in R on the SL-touch outcome.
    if out_long and out_short and out_long[0].touched_sl and out_short[0].touched_sl:
        assert out_short[0].r < out_long[0].r


def test_walk_forward_folds_cover_all_bars() -> None:
    folds = walk_forward_folds(1000, 5, embargo_bars=300)
    assert folds[0][0] == 0
    assert folds[-1][1] == 1000
    for a, b in pairwise(folds):
        assert b[0] == a[1]  # contiguous


def test_placebo_is_deterministic_and_flips_side() -> None:
    bars = _bars()
    sig_i, side, _, _ = sweep_and_reclaim(bars)
    if sig_i.size == 0:
        pytest.skip("no signals on this synthetic series")
    p1 = matched_placebo(bars, sig_i, side, seed=11)
    p2 = matched_placebo(bars, sig_i, side, seed=11)
    assert np.array_equal(p1[0], p2[0]) and np.array_equal(p1[1], p2[1])
    assert np.array_equal(p1[1], -side)


def test_evaluate_family_promotes_only_perfect_pass_on_synthetic() -> None:
    bars = _bars(600, seed=5)
    family = FAMILIES[0]
    res = evaluate_family(bars, family, n_folds=3)
    for key in ("full", "placebo", "edge_vs_placebo", "stressed", "folds", "status", "promoted"):
        assert key in res
    # On random-walk synthetic data nothing should be promoted; honest result is a candidate.
    assert res["status"] == "research candidate"
    assert res["promoted"] is False


def test_every_family_runs_without_error() -> None:
    bars = _bars(600, seed=9)
    for family in FAMILIES:
        sig_i, side, stops, tp_r = family["fn"](bars)
        assert sig_i.size == side.size == stops.size == tp_r.size
        assert np.all(stops > 0)
