from __future__ import annotations

import pytest

from mt5_intel.riskfit import (
    SymbolSpec,
    max_stop_at_min_lot,
    max_volume_for_budget,
    pip_size,
    pip_value_per_lot,
    reference_equity_usc,
    risk_budget_usc,
    risk_table,
)

USDJPY_CENT = SymbolSpec(
    name="USDJPYc",
    digits=3,
    point=0.001,
    tick_size=0.001,
    tick_value=0.6667,  # USC per tick per 1 lot at USD/JPY ~150
    volume_min=0.01,
    volume_step=0.01,
    volume_max=100.0,
)


def test_pip_size_for_three_digit_quote_is_point_times_ten() -> None:
    assert pip_size(USDJPY_CENT) == pytest.approx(0.01)


def test_pip_value_per_min_lot_matches_doc_example() -> None:
    # Design doc §19.4: about 0.067 US cents per pip at 0.01 lot.
    per_min_lot_per_pip = pip_value_per_lot(USDJPY_CENT) * USDJPY_CENT.volume_min
    assert per_min_lot_per_pip == pytest.approx(0.0667, rel=1e-3)


def test_reference_equity_is_r100_in_usc() -> None:
    # R100 at 18 ZAR per USD is about 555.6 USC.
    assert reference_equity_usc(100, 18) == pytest.approx(555.555, rel=1e-4)


def test_one_percent_budget_is_about_five_usc() -> None:
    assert risk_budget_usc(100, 18, 1.0) == pytest.approx(5.5555, rel=1e-4)


def test_max_volume_rounds_down_to_step() -> None:
    budget = risk_budget_usc(100, 18, 1.0)
    # 10 pips at 0.0667 USC/pip/min-lot leaves room for 0.08 lots (doc §19.4).
    assert max_volume_for_budget(USDJPY_CENT, 10, budget) == pytest.approx(0.08)


def test_stop_too_wide_for_budget_is_unsuitable() -> None:
    budget = risk_budget_usc(100, 18, 1.0)
    rows = {row.stop_pips: row for row in risk_table(USDJPY_CENT, budget)}
    assert rows[100].fits is False
    assert rows[100].max_volume == 0.0
    assert rows[10].fits is True


def test_actual_risk_never_exceeds_budget() -> None:
    budget = risk_budget_usc(100, 18, 1.0)
    for row in risk_table(USDJPY_CENT, budget):
        assert row.actual_risk <= budget + 1e-9


def test_max_stop_at_min_lot_is_about_eighty_pips() -> None:
    budget = risk_budget_usc(100, 18, 1.0)
    assert max_stop_at_min_lot(USDJPY_CENT, budget) == pytest.approx(83.3, rel=0.02)


def test_non_positive_tick_value_is_rejected() -> None:
    broken = SymbolSpec("X", 3, 0.001, 0.001, 0.0, 0.01, 0.01, 100.0)
    with pytest.raises(ValueError):
        pip_value_per_lot(broken)
