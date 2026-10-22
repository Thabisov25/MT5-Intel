"""Pure position-sizing maths from real symbol specs. Amounts are in account currency (USC on Cent)."""

from __future__ import annotations

import math
from dataclasses import dataclass

RISK_STOPS_PIPS: tuple[float, ...] = (10.0, 20.0, 30.0, 50.0, 80.0, 100.0)
USC_PER_USD = 100.0


@dataclass(frozen=True)
class SymbolSpec:
    name: str
    digits: int
    point: float
    tick_size: float
    tick_value: float  # per tick, per 1 lot, in account currency
    volume_min: float
    volume_step: float
    volume_max: float


@dataclass(frozen=True)
class RiskRow:
    stop_pips: float
    risk_per_min_lot: float  # account currency
    max_volume: float  # 0.0 when even the minimum lot exceeds the budget
    actual_risk: float  # account currency at max_volume
    fits: bool


def pip_size(spec: SymbolSpec) -> float:
    return spec.point * 10 if spec.digits in (3, 5) else spec.point


def pip_value_per_lot(spec: SymbolSpec) -> float:
    if spec.tick_size <= 0 or spec.tick_value <= 0:
        raise ValueError(f"{spec.name}: tick size and tick value must be positive")
    return spec.tick_value * pip_size(spec) / spec.tick_size


def max_volume_for_budget(spec: SymbolSpec, stop_pips: float, budget: float) -> float:
    per_lot = pip_value_per_lot(spec) * stop_pips
    if per_lot <= 0:
        return 0.0
    steps = math.floor(budget / per_lot / spec.volume_step + 1e-9)
    volume = min(round(steps * spec.volume_step, 8), spec.volume_max)
    return volume if volume >= spec.volume_min else 0.0


def risk_table(
    spec: SymbolSpec, budget: float, stops: tuple[float, ...] = RISK_STOPS_PIPS
) -> list[RiskRow]:
    rows: list[RiskRow] = []
    for stop in stops:
        volume = max_volume_for_budget(spec, stop, budget)
        rows.append(
            RiskRow(
                stop_pips=stop,
                risk_per_min_lot=pip_value_per_lot(spec) * spec.volume_min * stop,
                max_volume=volume,
                actual_risk=pip_value_per_lot(spec) * volume * stop,
                fits=volume > 0,
            )
        )
    return rows


def max_stop_at_min_lot(spec: SymbolSpec, budget: float) -> float:
    return budget / (pip_value_per_lot(spec) * spec.volume_min)


def reference_equity_usc(reference_zar: float, usd_zar: float) -> float:
    return reference_zar / usd_zar * USC_PER_USD


def risk_budget_usc(reference_zar: float, usd_zar: float, risk_pct: float) -> float:
    return reference_equity_usc(reference_zar, usd_zar) * risk_pct / 100


def usc_to_zar(amount_usc: float, usd_zar: float) -> float:
    return amount_usc / USC_PER_USD * usd_zar
