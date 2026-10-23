"""Hyperopt scored the way the research tables score: expectancy in R.

Every trade's result is divided by the risk that trade actually carried - its own stop
distance, which is ATR-based and therefore different per trade - so a winner on a wide-stop
week does not outweigh a loser on a tight-stop week the way raw currency would make it.
Expectancy is then the classic (win_probability * average_win) - (loss_probability * average_loss),
in R, and freqtrade minimises, so the loss is its negation.

A trade with no usable stop distance (stop_loss_ratio missing or ~0, which only a broken
adapter could produce) is skipped rather than counted as infinite R: garbage in one epoch's
trades must not steer the whole search.
"""

from __future__ import annotations

from datetime import datetime
from pandas import DataFrame

from freqtrade.optimize.hyperopt_loss.hyperopt_loss_interface import IHyperOptLoss


class ExpectancyRHyperOptLoss(IHyperOptLoss):
    @staticmethod
    def hyperopt_loss_function(
        results: DataFrame,
        trade_count: int,
        min_date: datetime,
        max_date: datetime,
        config: dict,
        *args,
        **kwargs,
    ) -> float:
        if trade_count == 0 or results is None or len(results) == 0:
            return 100.0  # no trades is the worst answer, not an undefined one

        # R = profit / risk, per trade. `profit_ratio` is profit/stake and `stop_loss_ratio`
        # is stop-distance/stake, so their quotient is profit/risk in R directly - no stake
        # column needed (hyperopt's frame does not carry one, only the exported results do).
        if "stop_loss_ratio" not in results.columns:
            return 100.0  # a frame without stop distances cannot be scored in R
        stop_r = results["stop_loss_ratio"].astype(float).abs()
        usable = stop_r > 1e-6
        if not usable.any():
            return 100.0

        r_multiple = results.loc[usable, "profit_ratio"].astype(float) / stop_r[usable]
        wins = r_multiple[r_multiple > 0]
        losses = r_multiple[r_multiple < 0]
        win_p = len(wins) / len(r_multiple)
        loss_p = len(losses) / len(r_multiple)
        avg_win = wins.mean() if len(wins) else 0.0
        avg_loss = -losses.mean() if len(losses) else 0.0
        expectancy_r = (win_p * avg_win) - (loss_p * avg_loss)
        return -float(expectancy_r)
