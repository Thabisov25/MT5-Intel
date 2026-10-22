from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fakes import FakeMT5
from mt5_intel.config import Settings
from mt5_intel.diagnostic import FAIL, INFO, NOT_VERIFIED, SAFE_REQUEST_BARS, WARN, run_diagnostic
from mt5_intel.report import to_markdown, write_report

WEDNESDAY_10_UTC = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)
SATURDAY = datetime(2026, 10, 10, 10, 0, tzinfo=UTC)


def _settings(tmp_path: Path, watch_s: float = 30.0) -> Settings:
    return Settings(
        login=12345678,
        password="unit-test-secret",
        server="HFMarketsSV-Demo",
        terminal_path=None,
        utc_offset_hours=None,
        symbol_root="USDJPY",
        reference_equity_zar=100.0,
        risk_pct=1.0,
        usd_zar_fallback=18.0,
        history_bars=5000,
        spread_days=2,
        tick_watch_seconds=watch_s,
        report_dir=tmp_path,
    )


def _statuses(report) -> dict[tuple[str, str], str]:  # type: ignore[no-untyped-def]
    return {(c.area, c.name): c.status for c in report.checks}


def test_happy_path_on_open_market_has_no_failures(tmp_path: Path) -> None:
    backend = FakeMT5(now=WEDNESDAY_10_UTC)
    report = run_diagnostic(_settings(tmp_path), backend, now=WEDNESDAY_10_UTC, sleep=lambda s: None)
    statuses = _statuses(report)
    assert statuses[("account", "currency")] == "PASS"
    assert statuses[("account", "mode")] == "PASS"
    assert statuses[("symbol", "identify")] == "PASS"
    assert statuses[("tick", "liveness")] == "PASS"
    assert statuses[("time", "utc_offset")] == "PASS"
    assert statuses[("rate", "usd_zar")] == WARN  # no USDZAR symbol in the fake: fallback is flagged
    assert report.overall == WARN
    assert report.data["utc_offset"]["seconds"] == 3 * 3600
    risk = report.data["risk"]
    assert risk["risk_budget_usc"] == pytest.approx(5.5555, rel=1e-3)
    assert report.data["history"]["M1"]["gaps_unexpected"] >= 1  # the two-hour outage is found
    assert report.data["history"]["M1"]["utc_classified"] is True
    assert len(report.data["spread_by_hour"]["hours"]) == 24


def test_password_is_passed_to_terminal_but_never_reported(tmp_path: Path) -> None:
    backend = FakeMT5(now=WEDNESDAY_10_UTC)
    report = run_diagnostic(_settings(tmp_path), backend, now=WEDNESDAY_10_UTC, sleep=lambda s: None)
    assert backend.init_kwargs["password"] == "unit-test-secret"
    md = to_markdown(report)
    json_path, md_path = write_report(report, tmp_path, WEDNESDAY_10_UTC)
    text = md + md_path.read_text(encoding="utf-8") + json_path.read_text(encoding="utf-8")
    assert "unit-test-secret" not in text
    assert "12345678" not in text  # login is masked in the report
    assert "***678" in text


def test_live_usd_zar_rate_is_used_when_symbol_exists(tmp_path: Path) -> None:
    backend = FakeMT5(now=WEDNESDAY_10_UTC, usd_zar_symbol=True)
    report = run_diagnostic(_settings(tmp_path), backend, now=WEDNESDAY_10_UTC, sleep=lambda s: None)
    assert _statuses(report)[("rate", "usd_zar")] == "PASS"
    assert report.data["usd_zar"]["source"] == "MT5 symbol USDZAR"


def test_real_or_non_cent_account_fails(tmp_path: Path) -> None:
    backend = FakeMT5(now=WEDNESDAY_10_UTC, currency="USD", trade_mode=2)
    report = run_diagnostic(_settings(tmp_path), backend, now=WEDNESDAY_10_UTC, sleep=lambda s: None)
    statuses = _statuses(report)
    assert statuses[("account", "currency")] == FAIL
    assert statuses[("account", "mode")] == FAIL
    assert report.overall == FAIL


def test_stale_feed_on_open_market_warns(tmp_path: Path) -> None:
    backend = FakeMT5(now=WEDNESDAY_10_UTC, advance_ticks=False)
    report = run_diagnostic(_settings(tmp_path, watch_s=0.0), backend, now=WEDNESDAY_10_UTC,
                            sleep=lambda s: None)
    assert _statuses(report)[("tick", "liveness")] == WARN
    assert "utc_offset" in report.data and report.data["utc_offset"]["seconds"] == 3 * 3600


def test_closed_market_skips_staleness_and_marks_offset_unknown(tmp_path: Path) -> None:
    backend = FakeMT5(now=SATURDAY)
    report = run_diagnostic(_settings(tmp_path), backend, now=SATURDAY, sleep=lambda s: None)
    statuses = _statuses(report)
    assert statuses[("tick", "liveness")] == "INFO"
    assert statuses[("time", "utc_offset")] == NOT_VERIFIED
    assert report.data["history"]["M1"]["utc_classified"] is False


def test_missing_symbol_fails_closed(tmp_path: Path) -> None:
    backend = FakeMT5(now=WEDNESDAY_10_UTC, symbol_name="")
    report = run_diagnostic(_settings(tmp_path), backend, now=WEDNESDAY_10_UTC, sleep=lambda s: None)
    assert _statuses(report)[("symbol", "identify")] == FAIL
    assert "risk" not in report.data


def test_history_request_above_terminal_maxbars_is_capped(tmp_path: Path) -> None:
    backend = FakeMT5(now=WEDNESDAY_10_UTC, maxbars=100_000)
    settings = replace(_settings(tmp_path), history_bars=300_000)
    report = run_diagnostic(settings, backend, now=WEDNESDAY_10_UTC, sleep=lambda s: None)
    statuses = _statuses(report)
    assert statuses[("history", "bar_cap")] == INFO
    assert statuses[("history", "M5")] == INFO  # served after capping, not NOT_VERIFIED
    assert report.data["history"]["M5"]["bars"] <= SAFE_REQUEST_BARS


def test_history_failure_reports_terminal_error(tmp_path: Path) -> None:
    backend = FakeMT5(now=WEDNESDAY_10_UTC, no_history=True)
    report = run_diagnostic(_settings(tmp_path), backend, now=WEDNESDAY_10_UTC, sleep=lambda s: None)
    detail = next(c.detail for c in report.checks if c.area == "history" and c.name == "M1")
    assert _statuses(report)[("history", "M1")] == NOT_VERIFIED
    assert "Invalid params" in detail


def test_zero_tick_value_right_after_connect_is_retried(tmp_path: Path) -> None:
    backend = FakeMT5(now=WEDNESDAY_10_UTC, spec_warmup_reads=2)
    report = run_diagnostic(_settings(tmp_path), backend, now=WEDNESDAY_10_UTC, sleep=lambda s: None)
    assert ("risk", "table") not in _statuses(report)  # no NOT_VERIFIED: the retry succeeded
    assert "risk" in report.data


def test_persistent_zero_tick_value_still_fails_closed(tmp_path: Path) -> None:
    backend = FakeMT5(now=WEDNESDAY_10_UTC, spec_warmup_reads=99)
    report = run_diagnostic(_settings(tmp_path), backend, now=WEDNESDAY_10_UTC, sleep=lambda s: None)
    assert _statuses(report)[("risk", "table")] == NOT_VERIFIED
    assert "risk" not in report.data


def test_failed_login_raises_connection_error(tmp_path: Path) -> None:
    backend = FakeMT5(now=WEDNESDAY_10_UTC, init_ok=False)
    with pytest.raises(ConnectionError):
        run_diagnostic(_settings(tmp_path), backend, now=WEDNESDAY_10_UTC, sleep=lambda s: None)
    assert "shutdown" not in backend.calls  # nothing was connected, so nothing to shut down
