from __future__ import annotations

from mt5_intel.gate import GateInputs, evaluate


def test_healthy_open_market_with_validated_strategy_is_actionable() -> None:
    gate = evaluate(
        GateInputs(
            bar_age_s=30,
            spread_pips=1.5,
            market_open=True,
            news_verifiable=True,
            news_excluded=False,
            circuit_breaker_tripped=False,
            daily_loss_pct=0.0,
            open_positions=0,
            validated_strategy="sweep-reclaim-v1",
            risk_fits=True,
        )
    )
    assert gate.actionable
    assert gate.reason_codes == []


def test_every_failure_is_reported_not_first_only() -> None:
    gate = evaluate(
        GateInputs(
            bar_age_s=9999,
            spread_pips=99.0,
            market_open=False,
            news_verifiable=False,
            circuit_breaker_tripped=True,
            daily_loss_pct=5.0,
            open_positions=3,
            risk_fits=False,
        )
    )
    assert gate.verdict == "SKIP"
    for expected in (
        "DATA_STALE",
        "SPREAD_TOO_WIDE",
        "MARKET_CLOSED",
        "NEWS_UNVERIFIED",
        "FAST_MOVE_CIRCUIT_BREAKER",
        "DAILY_LOSS_LIMIT",
        "POSITION_LIMIT",
        "NO_VALIDATED_STRATEGY",
        "RISK_EXCEEDS_BUDGET",
    ):
        assert expected in gate.reason_codes
    assert len(gate.blockers) == len(gate.reason_codes)


def test_unverifiable_values_default_deny() -> None:
    gate = evaluate(GateInputs())  # everything None/False
    assert gate.verdict == "SKIP"
    for expected in (
        "DATA_STALE_OR_UNVERIFIED",
        "SPREAD_UNVERIFIED",
        "MARKET_STATE_UNVERIFIED",
        "NEWS_UNVERIFIED",
        "DAILY_LOSS_UNVERIFIED",
        "POSITIONS_UNVERIFIED",
        "NO_VALIDATED_STRATEGY",
        "RISK_FIT_UNVERIFIED",
    ):
        assert expected in gate.reason_codes


def test_unknown_gate_about_nothing_actionable_without_strategy() -> None:
    # Even a perfect live market cannot produce a card without a validated strategy.
    gate = evaluate(
        GateInputs(
            bar_age_s=30,
            spread_pips=1.5,
            market_open=True,
            news_verifiable=True,
            news_excluded=False,
            circuit_breaker_tripped=False,
            daily_loss_pct=0.0,
            open_positions=0,
            risk_fits=True,
            validated_strategy=None,
        )
    )
    assert not gate.actionable
    assert gate.reason_codes == ["NO_VALIDATED_STRATEGY"]
