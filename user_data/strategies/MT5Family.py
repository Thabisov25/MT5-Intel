"""The mt5-intel family rules, wrapped as freqtrade strategies for offline backtesting.

The point of this adapter is that **the signal logic is not ported - it is imported**. The
family functions from `mt5_intel.research.strategies` are the same objects the research
tables and the live cards are graded by, so a freqtrade backtest here cannot drift from the
replays: same bars, same rules, same stops. What freqtrade brings is the engine around them -
candle-accurate fills, the trade lifecycle, and hyperopt.

Two knobs sit on the shared base and apply to every family:

* `tp_r` - the planned target, in R. The families' own value is 2.0; lowering it raises the
  win rate mechanically (closer targets hit more often) and is exactly the trade the trust
  search has to measure rather than assume, so it is a searched dimension, not a rewrite.
* `stop_scale` - a multiplier on each signal's own stop distance. 1.0 is the family's stop.

And one rule that is **not** a knob, because it is not this file's to choose: the live watcher
moves a stop up once a trade is in profit (lock `AUTO_LOCK_R` at `AUTO_BREAKEVEN_R`, then trail
`AUTO_TRAIL_DISTANCE_R` behind price from `AUTO_TRAIL_R`). `custom_stoploss` reproduces those two
rules from the same .env values, so a backtest here measures the system that actually trades
rather than one with fixed barriers and a broker that never protects a winner.

What is approximated, stated plainly:

* **Entry price** is the next candle's open (freqtrade's default), which is what the research
  rules assume (D-012).
* **Stop and target** are enforced by `custom_stoploss` / `custom_exit` as absolute prices
  derived from the signal bar's stop-pips and R-multiple, exactly as the mt5-intel sizing
  does - but freqtrade marks exits at the candle's own price, where the live engine places a
  broker-side stop order. On a gappy market those differ by the gap.
* **Spread** is not charged by freqtrade (the exported candles carry no cost model); the
  research pages charge it in R via `cost_in_r`. Read the expectancy numbers as pre-spread.
* **The short side is ignored**: the pilot runs on spot data (the only supported exchange
  whose markets contain these FX pairs offers no futures/margin mode inside freqtrade), so
  `can_short = False` and the research replays remain the authority for shorts.

Bars arrive as UTC (the exporter resolved the broker clock offset before writing), so
`utc_offset_s=0` here is a fact about the file, not an assumption about the market.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from freqtrade.persistence import Trade
from freqtrade.strategy import CategoricalParameter, DecimalParameter, IStrategy, IntParameter

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))


# The live watcher's stop management, in force in a backtest too.
#
# `execution._manage_auto_stops` moves a stop up once a trade is in profit - the lock that keeps
# a working trade from round-tripping into a loss, and the trail that follows it after that. A
# backtest without those rules measures a system that does not exist, and the losses it shows are
# not the losses the account would take. So the same two rules run here.
#
# The runner exports these from `mt5_intel.config.load_stop_manage()`, which is the same reader
# the watcher uses, so one line in the .env moves both engines together. The defaults below are
# that function's own, for a strategy loaded outside the runner (hyperopt, a bare freqtrade
# command) - they can never disagree with a .env, because they are only reached when there is no
# .env in play.
def _env_flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


MANAGE_ON = _env_flag("MT5INTEL_MANAGE", "1")
BREAKEVEN_R = float(os.environ.get("MT5INTEL_BREAKEVEN_R", "1.0"))
LOCK_R = float(os.environ.get("MT5INTEL_LOCK_R", "0.1"))
TRAIL_R = float(os.environ.get("MT5INTEL_TRAIL_R", "2.0"))
TRAIL_DISTANCE_R = float(os.environ.get("MT5INTEL_TRAIL_DISTANCE_R", "1.0"))

from mt5_intel.research.backtest import Bars  # noqa: E402
from mt5_intel.research.strategies import (  # noqa: E402
    carry_trend,
    compression_breakout,
    daily_momentum,
    ma_crossover,
    range_extreme_reversion,
    rsi_mean_reversion,
    sweep_and_reclaim,
    trend_pullback,
)


def _digits_for(pair: str) -> int:
    """Quote precision the way its market quotes it: JPY 3, gold 2, everything else 5."""
    base, _, quote = pair.partition("/")
    if quote == "JPY":
        return 3
    if base == "XAU":
        return 2
    return 5


def dataframe_to_bars(df: pd.DataFrame, pair: str) -> Bars:
    """The analysed frame back to the shape every family rule has always taken.

    `date` comes in as millisecond stamps (the handlers' format) and `Bars.time` counts
    UTC seconds; `spread_points` is carried through the export so the stops see the real
    spread distribution even though freqtrade itself charges no cost.
    """
    digits = _digits_for(pair)
    point = 10.0 ** (-digits)
    spread = (
        df["spread_points"].to_numpy(dtype=np.int32)
        if "spread_points" in df.columns
        else np.zeros(len(df), dtype=np.int32)
    )
    return Bars(
        time=(df["date"].astype("int64") // 1000).to_numpy(),
        open_=df["open"].to_numpy(dtype="float64"),
        high=df["high"].to_numpy(dtype="float64"),
        low=df["low"].to_numpy(dtype="float64"),
        close=df["close"].to_numpy(dtype="float64"),
        spread_points=spread,
        point=point,
        digits=digits,
        utc_offset_s=0,  # the exporter already put these stamps on UTC
    )


class MT5FamilyBase(IStrategy):
    """Shared plumbing: family signals in, freqtrade's trade lifecycle out.

    Subclasses set `family_fn` and declare their own hyperopt space. The interface version,
    the empty exit table and the R-multiple management live here once.
    """

    INTERFACE_VERSION = 3
    timeframe = "1h"
    # The families signal both directions, but the pilot runs on spot data - the only
    # freqtrade-supported exchange whose markets contain these FX pairs (kraken) offers no
    # futures or margin mode inside freqtrade - so the short side is ignored by the engine
    # and the research replays remain its authority. Everything else (stops, targets,
    # hyperopt) is symmetric between the sides, so what is measured here transfers.
    can_short = False
    # ROI and the static stoploss are inert: the real stop is pinned per trade by
    # custom_stoploss at the family's own stop distance, and the target is the family's own
    # R-multiple. Freqtrade needs *some* stoploss value; -0.99 is the documented "managed
    # entirely by custom_stoploss" idiom, and a threshold no price can reach is how the ROI
    # table says "nothing here" - a -100 threshold would fire at any profit above -100%,
    # which is every trade.
    minimal_roi = {"0": 9999.9}
    stoploss = -0.99
    use_custom_stoploss = True
    trailing_stop = False
    process_only_new_candles = True

    family_fn = None  # set by the subclass

    # The two shared searched dimensions. Defaults are the families' own values, so an
    # un-hyperopted run is exactly the recorded rule.
    tp_r = DecimalParameter(1.0, 3.0, default=2.0, decimals=1, space="buy", optimize=True)
    stop_scale = DecimalParameter(0.5, 2.0, default=1.0, decimals=2, space="buy", optimize=True)

    def populate_indicators(self, df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        bars = dataframe_to_bars(df, metadata["pair"])
        sig, side, stop_pips, _fn_tp_r = self.family_fn(bars, **self.family_params())
        df["enter_long"] = 0
        df["enter_short"] = 0
        df["stop_ratio"] = 0.0
        df["tp_r"] = float(self.tp_r.value)
        if sig.size:
            # Entry is the next bar's open; the stop distance is expressed as a ratio of the
            # signal bar's close so the absolute stop can be rebuilt from the trade's own
            # entry price later, the way the sizing does it live. `stop_scale` stretches or
            # tightens the family's own stop as one searched dimension.
            scaled = stop_pips * float(self.stop_scale.value)
            ratio = scaled * bars.pip() / np.maximum(bars.close[sig], 1e-12)
            long_rows = sig[side > 0]
            short_rows = sig[side < 0]
            df.iloc[long_rows, df.columns.get_loc("enter_long")] = 1
            df.iloc[short_rows, df.columns.get_loc("enter_short")] = 1
            df.iloc[sig, df.columns.get_loc("stop_ratio")] = ratio
        return df

    def populate_entry_trend(self, df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        return df

    def populate_exit_trend(self, df: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        return df  # exits are the stop and the R-multiple target, managed per trade

    def family_params(self) -> dict:
        return {}

    def _signal_row(self, trade: Trade, current_time) -> pd.DataFrame | None:
        """The analysed row the entry was signalled on - the bar before the entry bar."""
        df, _ = self.dp.get_analyzed_dataframe(trade.pair, self.timeframe)
        if df is None or df.empty:
            return None
        entries = df[(df["enter_long"] == 1) | (df["enter_short"] == 1)]
        prior = entries[entries["date"] < trade.open_date]
        return prior.iloc[-1] if len(prior) else None

    def custom_stoploss(self, trade: Trade, current_time, current_rate, after_fill, **kwargs) -> float:
        """The family's own stop, plus the live watcher's two rules that move it up.

        The family's stop is pinned at the distance from entry its signal produced, expressed the
        way freqtrade wants it - a ratio from the *current* rate. On top of that sit the same two
        rules `execution._manage_auto_stops` sends to the broker live:

        * at `BREAKEVEN_R` of profit the stop locks `LOCK_R` beyond entry, so a trade that worked
          can no longer round-trip into a loss;
        * at `TRAIL_R` it holds `TRAIL_DISTANCE_R` behind the current price, and every later
          improvement reapplies the same rule.

        Both only ever tighten. Freqtrade will not loosen a stop once set - the same ratchet the
        watcher enforces with its `improves` check - and a candidate that would sit at or past the
        target is dropped here exactly as the watcher drops it, so the target is still what closes
        that trade rather than the stop that reached it.
        """
        row = self._signal_row(trade, current_time)
        if row is None or row["stop_ratio"] <= 0:
            return 0.001  # nothing to pin from; keep the inert floor rather than guess
        stop_ratio = float(row["stop_ratio"])
        entry = float(trade.open_rate)
        long_side = not trade.is_short
        # One R in price terms: the stop distance the card was planned with, which is what makes
        # `profit_r` read as distance travelled in units of the risk taken.
        r_distance = entry * stop_ratio

        family_sl = entry * (1 - stop_ratio) if long_side else entry * (1 + stop_ratio)
        target = (
            entry * (1 + float(row["tp_r"]) * stop_ratio)
            if long_side
            else entry * (1 - float(row["tp_r"]) * stop_ratio)
        )

        stop_price = family_sl
        if MANAGE_ON and r_distance > 0:
            profit_r = (
                (current_rate - entry) if long_side else (entry - current_rate)
            ) / r_distance
            candidates = [family_sl]
            if profit_r >= BREAKEVEN_R:
                candidates.append(
                    entry + LOCK_R * r_distance if long_side else entry - LOCK_R * r_distance
                )
            if profit_r >= TRAIL_R:
                offset = TRAIL_DISTANCE_R * r_distance
                candidates.append(
                    current_rate - offset if long_side else current_rate + offset
                )
            stop_price = max(candidates) if long_side else min(candidates)
            # A stop at or past the target would have the broker closing the trade inside the tick;
            # the watcher skips the move rather than fight it, so the family's own stop stands and
            # freqtrade keeps whatever it already holds.
            if (long_side and stop_price >= target) or (
                not long_side and stop_price <= target
            ):
                stop_price = family_sl

        if long_side:
            distance = (current_rate - stop_price) / current_rate
        else:
            distance = (stop_price - current_rate) / current_rate
        return max(float(distance), 0.001)

    def custom_exit(self, trade: Trade, current_time, current_rate, current_profit, **kwargs) -> str | None:
        """The family's planned target: entry +/- tp_r * stop distance, in R."""
        row = self._signal_row(trade, current_time)
        if row is None or row["stop_ratio"] <= 0:
            return None
        stop_ratio = float(row["stop_ratio"])
        tp_r = float(row["tp_r"])
        if trade.is_short:
            target = trade.open_rate * (1 - tp_r * stop_ratio)
            hit = current_rate <= target
        else:
            target = trade.open_rate * (1 + tp_r * stop_ratio)
            hit = current_rate >= target
        return "target_r" if hit else None


class SweepReclaimFT(MT5FamilyBase):
    """Sweep of a confirmed swing level then reclaim close back inside (H-001).

    The hyperopt space mirrors the research sweep: the swing lookback, the "swept enough"
    ATR multiple (0.05/0.15 were the research values) and the reclaim window are the
    parameters the recorded trials varied.
    """

    family_fn = staticmethod(sweep_and_reclaim)

    k = IntParameter(3, 8, default=5, space="buy", optimize=True)
    atr_mult = DecimalParameter(0.05, 0.40, default=0.05, decimals=2, space="buy", optimize=True)
    confirm_bars = IntParameter(1, 3, default=1, space="buy", optimize=True)

    def family_params(self) -> dict:
        return {"k": int(self.k.value), "atr_mult": float(self.atr_mult.value), "confirm_bars": int(self.confirm_bars.value)}


class CarryTrendFT(MT5FamilyBase):
    """Carry-gated trend (H-007): policy-rate spread agrees with close-vs-MA trend, else flat.

    `ma_days` is expressed in bars here (the family counts bars, not days), so the default
    200 matches the live rule's MA(200) on H1. The policy-rate table is the same one the
    live card reads - it is imported, not reimplemented.
    """

    family_fn = staticmethod(carry_trend)

    ma_days = IntParameter(100, 400, default=200, space="buy", optimize=True)
    stop_atr = DecimalParameter(2.0, 8.0, default=4.0, decimals=1, space="buy", optimize=True)

    def family_params(self) -> dict:
        return {"ma_days": int(self.ma_days.value), "stop_atr": float(self.stop_atr.value)}


class CompressionBreakoutFT(MT5FamilyBase):
    """Range compression then breakout close outside the box (H-002)."""

    family_fn = staticmethod(compression_breakout)

    lookback = IntParameter(24, 96, default=48, space="buy", optimize=True)
    squeeze_q = DecimalParameter(0.05, 0.60, default=0.25, decimals=2, space="buy", optimize=True)

    def family_params(self) -> dict:
        return {"lookback": int(self.lookback.value), "squeeze_q": float(self.squeeze_q.value)}


class TrendPullbackFT(MT5FamilyBase):
    """EMA trend filter; enter on a k_atr pullback toward the fast EMA (H-004)."""

    family_fn = staticmethod(trend_pullback)

    fast = IntParameter(20, 100, default=50, space="buy", optimize=True)
    slow_gap = IntParameter(50, 200, default=150, space="buy", optimize=True)
    k_atr = DecimalParameter(1.0, 3.0, default=1.5, decimals=1, space="buy", optimize=True)

    def family_params(self) -> dict:
        # The rule raises when fast >= slow; the gap parameter makes that impossible by
        # construction rather than by hoping the sampler stays inside the lines.
        fast = int(self.fast.value)
        return {"fast": fast, "slow": fast + int(self.slow_gap.value), "k_atr": float(self.k_atr.value)}


class RangeExtremeFT(MT5FamilyBase):
    """Edge of a multi-hour range with stretched RSI -> fade to the middle (H-005).

    One level dimension instead of two: `lo_rsi` is mirrored from `hi_rsi` (100 - hi), the
    symmetric reading the family's own default (65/35) encodes.
    """

    family_fn = staticmethod(range_extreme_reversion)

    lookback = IntParameter(48, 192, default=96, space="buy", optimize=True)
    hi_rsi = DecimalParameter(55.0, 80.0, default=65.0, decimals=1, space="buy", optimize=True)

    def family_params(self) -> dict:
        hi = float(self.hi_rsi.value)
        return {"lookback": int(self.lookback.value), "hi_rsi": hi, "lo_rsi": 100.0 - hi}


class DailyMomentumFT(MT5FamilyBase):
    """MOP-style time-series momentum on the trailing log return (H-006)."""

    family_fn = staticmethod(daily_momentum)

    lookback = IntParameter(48, 240, default=120, space="buy", optimize=True)
    stop_atr = DecimalParameter(1.5, 5.0, default=3.0, decimals=1, space="buy", optimize=True)

    def family_params(self) -> dict:
        return {"lookback": int(self.lookback.value), "stop_atr": float(self.stop_atr.value)}


class MaCrossoverFT(MT5FamilyBase):
    """Fast/slow MA crossover with the hysteresis band (H-008).

    The slow average is expressed as a gap above the fast one so `fast < slow` holds by
    construction. `buffer_atr` is the whipsaw tolerance: 0.0 is the textbook razor edge.
    """

    family_fn = staticmethod(ma_crossover)

    fast = IntParameter(10, 50, default=20, space="buy", optimize=True)
    slow_gap = IntParameter(15, 150, default=30, space="buy", optimize=True)
    kind = CategoricalParameter(["sma", "ema"], default="sma", space="buy", optimize=True)
    buffer_atr = DecimalParameter(0.0, 1.5, default=0.0, decimals=2, space="buy", optimize=True)
    stop_atr = DecimalParameter(0.75, 4.0, default=1.5, decimals=2, space="buy", optimize=True)

    def family_params(self) -> dict:
        fast = int(self.fast.value)
        return {
            "fast": fast,
            "slow": fast + int(self.slow_gap.value),
            "kind": str(self.kind.value),
            "buffer_atr": float(self.buffer_atr.value),
            "stop_atr": float(self.stop_atr.value),
        }


class RsiMeanReversionFT(MT5FamilyBase):
    """RSI arm-and-confirm mean reversion (H-009). Levels mirrored as 100 - hi."""

    family_fn = staticmethod(rsi_mean_reversion)

    rsi_period = IntParameter(2, 20, default=14, space="buy", optimize=True)
    hi = DecimalParameter(60.0, 90.0, default=70.0, decimals=1, space="buy", optimize=True)
    entry = CategoricalParameter(["confirm", "immediate"], default="confirm", space="buy", optimize=True)
    confirm_bars = IntParameter(1, 6, default=3, space="buy", optimize=True)
    stop_atr = DecimalParameter(1.0, 4.0, default=1.5, decimals=1, space="buy", optimize=True)

    def family_params(self) -> dict:
        hi = float(self.hi.value)
        return {
            "rsi_period": int(self.rsi_period.value),
            "hi": hi,
            "lo": 100.0 - hi,
            "entry": str(self.entry.value),
            "confirm_bars": int(self.confirm_bars.value),
            "stop_atr": float(self.stop_atr.value),
        }
