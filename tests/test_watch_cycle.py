from __future__ import annotations

import inspect
import json
import os
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from fakes import FakeMT5
from mt5_intel import watch
from mt5_intel.config import Settings
from mt5_intel.mt5_adapter import ReadOnlyMT5
from mt5_intel.research.backtest import Bars
from mt5_intel.store import Store

WEDNESDAY_10_UTC = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)


@pytest.fixture()
def no_notify(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Keep tests offline: no toasts, no email attempts."""
    def _stub(*_args: object, **_kwargs: object) -> None:
        return None
    monkeypatch.setattr("mt5_intel.watch.notify_alert", _stub)
    return _stub


def _settings(tmp_path: Path, symbol: str = "USDJPY", history_bars: int = 600) -> Settings:
    return Settings(
        login=12345678,
        password="unit-test-secret",
        server="HFMarketsSV-Demo",
        terminal_path=None,
        utc_offset_hours=None,
        symbol_root=symbol,
        reference_equity_zar=100.0,
        risk_pct=1.0,
        # What from_env maps too: the auto risk falls back to RISK_PCT when no
        # AUTO_TRADE_RISK_PCT is given, so a Settings built by hand carries the same pairing
        # rather than the dataclass's nothing-budget - that contract stays with the dataclass,
        # where execution's sizing test pins it.
        auto_risk_pct=1.0,
        usd_zar_fallback=18.0,
        history_bars=history_bars,
        spread_days=2,
        tick_watch_seconds=0.0,
        report_dir=tmp_path,
    )


def test_cycle_journals_skip_and_writes_status(tmp_path: Path, no_notify: Callable[..., None]) -> None:
    store = Store(tmp_path / "mt5.db")
    backend = FakeMT5(now=WEDNESDAY_10_UTC, symbol_name="USDJPY")
    settings = _settings(tmp_path)
    summary = watch._journal_and_decide(store, settings, watch._read_state(
        # The adapter wraps the backend; build it the way the loop does.
        __import__("mt5_intel.mt5_adapter", fromlist=["ReadOnlyMT5"]).ReadOnlyMT5(backend),
        settings.symbol_root,
    ), int(WEDNESDAY_10_UTC.timestamp()))

    assert summary["gate_verdict"] == "SKIP"
    assert "NO_VALIDATED_STRATEGY" in summary["gate_reasons"]
    assert summary["connected"] is True
    assert "bar_epoch" in summary

    recs = store.recent_recommendations(limit=1)
    assert recs[0]["verdict"] == "SKIP"
    # Fake tick 150.100/150.114 = 14 points = 1.4 pips on a 3-digit JPY quote.
    assert recs[0]["payload"]["spread_pips"] == pytest.approx(1.4, abs=0.01)


def test_second_cycle_same_bar_dedupes_recommendations(
    tmp_path: Path, no_notify: Callable[..., None]
) -> None:
    store = Store(tmp_path / "mt5.db")
    backend = FakeMT5(now=WEDNESDAY_10_UTC, symbol_name="USDJPY")
    settings = _settings(tmp_path)
    adapter = __import__("mt5_intel.mt5_adapter", fromlist=["ReadOnlyMT5"]).ReadOnlyMT5(backend)
    state = watch._read_state(adapter, settings.symbol_root)
    watch._journal_and_decide(store, settings, state, int(WEDNESDAY_10_UTC.timestamp()))
    watch._journal_and_decide(store, settings, state, int(WEDNESDAY_10_UTC.timestamp()) + 5)
    # Same bar → dedupe keeps one recommendation row.
    assert len(store.recent_recommendations(limit=10)) == 1


def test_disconnected_backend_defaults_deny(tmp_path: Path, no_notify: Callable[..., None]) -> None:
    backend = FakeMT5(now=WEDNESDAY_10_UTC, init_ok=False)
    settings = _settings(tmp_path)
    # init failure raises inside connect(); the loop's except branch journals an error instead.
    from mt5_intel.mt5_adapter import ReadOnlyMT5
    adapter = ReadOnlyMT5(backend)
    with pytest.raises(ConnectionError):
        adapter.connect(login=settings.login, password=settings.password, server=settings.server, terminal_path=None)


def test_status_file_roundtrip(
    tmp_path: Path, no_notify: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(watch, "STATUS_FILE", tmp_path / "live-status.json")
    payload = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "watcher_alive": True,
        "connected": True,
        "gate_verdict": "SKIP",
        "gate_reasons": ["NO_VALIDATED_STRATEGY"],
        "bar_epoch": 1000,
        "bar_age_s": 12,
        "spread_pips": 1.4,
        "rec_id": 7,
        "symbol": "USDJPY",
    }
    watch._atomic_write_json(watch.STATUS_FILE, payload)
    loaded = json.loads((tmp_path / "live-status.json").read_text(encoding="utf-8"))
    assert loaded["rec_id"] == 7
    assert loaded["gate_verdict"] == "SKIP"


def test_status_write_retries_a_held_file_instead_of_losing_the_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scanner, a sync client or the dashboard holding live-status.json must not cost the write.

    WinError 5 is what killed the watcher on 2026-10-12 22:14 UTC: the rename onto
    live-status.json was denied while the file was held open by something else, which is a
    momentary condition, not a permanent one.
    """
    target = tmp_path / "live-status.json"
    replace = os.replace
    attempts: list[int] = []

    def held_twice(src: str, dst: str) -> None:
        attempts.append(1)
        if len(attempts) <= 2:
            raise PermissionError(5, "Access is denied")
        replace(src, dst)

    monkeypatch.setattr(watch, "RENAME_RETRY_SLEEP_S", (0.0, 0.0, 0.0))
    monkeypatch.setattr(os, "replace", held_twice)
    watch._atomic_write_json(target, {"rec_id": 7})

    assert len(attempts) == 3, "the rename is retried, not abandoned on the first denial"
    assert json.loads(target.read_text(encoding="utf-8"))["rec_id"] == 7
    assert not (tmp_path / "live-status.tmp").exists()


def test_status_write_writes_in_place_when_the_rename_never_lands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A held file degrades the write, it does not cancel it: a view that never updates is worse
    than a reader catching a truncated file, which the dashboard already tolerates."""
    target = tmp_path / "live-status.json"

    def always_denied(src: str, dst: str) -> None:
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(watch, "RENAME_RETRY_SLEEP_S", (0.0, 0.0, 0.0))
    monkeypatch.setattr(os, "replace", always_denied)
    watch._atomic_write_json(target, {"rec_id": 9})

    assert json.loads(target.read_text(encoding="utf-8"))["rec_id"] == 9
    assert not (tmp_path / "live-status.tmp").exists()


def test_a_failing_status_write_is_journaled_and_never_stops_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The double fault that ended the watcher: the cycle handler's own status write raised too.

    A reporting artifact must never be able to stop the loop. `_write_status` is the single
    place the loop writes live-status.json, so this is where that has to hold.
    """
    store = Store(tmp_path / "mt5.db")

    def denied(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(watch, "_atomic_write_json", denied)
    watch._write_status(store, tmp_path / "live-status.json", {"watcher_alive": False})

    errors = store.recent_events("status_error")
    assert len(errors) == 1
    assert errors[0]["payload"]["error"] == "PermissionError"


def test_the_loop_writes_status_only_through_the_guard() -> None:
    """A raw status write in the loop is the line whose raise ended the process; keep it out.

    Read as source, because the failure it guards against is a *new* call site: the loop's two
    status writes and its error-path write are all `_write_status`, which journals instead of
    raising.
    """
    source = inspect.getsource(watch.main)
    assert "_write_status(" in source
    assert "_atomic_write_json(STATUS_FILE" not in source


def test_gate_inputs_come_from_reads_not_placeholders(
    tmp_path: Path, no_notify: Callable[..., None]
) -> None:
    """Exposure, the day's loss and the circuit breaker are measured, and the codes show it."""
    store = Store(tmp_path / "mt5.db")
    backend = FakeMT5(now=WEDNESDAY_10_UTC, symbol_name="USDJPY", positions=1)
    settings = _settings(tmp_path)
    adapter = ReadOnlyMT5(backend)
    now = int(WEDNESDAY_10_UTC.timestamp())
    summary = watch._journal_and_decide(
        store, settings, watch._read_state(adapter, settings.symbol_root), now
    )

    assert "POSITIONS_UNVERIFIED" not in summary["gate_reasons"]
    assert "POSITION_LIMIT" not in summary["gate_reasons"]  # one open against a cap of two
    assert "DAILY_LOSS_UNVERIFIED" not in summary["gate_reasons"]
    assert "FAST_MOVE_CIRCUIT_BREAKER" not in summary["gate_reasons"]  # the fake series is calm

    payload = store.recent_recommendations(limit=1)[0]["payload"]
    assert payload["positions"] == {"total": 1, "symbol": 1}
    # The offset comes from the live tick (GMT+3 here), so the day is the broker's day.
    assert payload["daily_loss_pct"] == 0.0
    assert payload["fast_move"]["measured"] is True
    assert payload["fast_move"]["tripped"] is False
    assert payload["fast_move"]["atr_pips"] > 0


def test_position_limit_trips_when_exposure_is_at_the_cap(
    tmp_path: Path, no_notify: Callable[..., None]
) -> None:
    store = Store(tmp_path / "mt5.db")
    backend = FakeMT5(now=WEDNESDAY_10_UTC, symbol_name="USDJPY", positions=2)
    settings = _settings(tmp_path)
    adapter = ReadOnlyMT5(backend)
    summary = watch._journal_and_decide(
        store, settings, watch._read_state(adapter, settings.symbol_root),
        int(WEDNESDAY_10_UTC.timestamp()),
    )
    assert "POSITION_LIMIT" in summary["gate_reasons"]
    assert "POSITIONS_UNVERIFIED" not in summary["gate_reasons"]


def test_daily_loss_is_measured_from_the_days_opening_equity(
    tmp_path: Path, no_notify: Callable[..., None]
) -> None:
    store = Store(tmp_path / "mt5.db")
    backend = FakeMT5(now=WEDNESDAY_10_UTC, equity=1000.0)
    settings = _settings(tmp_path)
    adapter = ReadOnlyMT5(backend)
    now = int(WEDNESDAY_10_UTC.timestamp())

    first = watch._journal_and_decide(
        store, settings, watch._read_state(adapter, settings.symbol_root), now
    )
    assert "DAILY_LOSS_LIMIT" not in first["gate_reasons"]

    # Ten percent down on the day, on the next bar.
    backend.equity = 900.0
    backend.now_epoch += 60
    second = watch._journal_and_decide(
        store, settings, watch._read_state(adapter, settings.symbol_root), now + 60
    )
    assert "DAILY_LOSS_LIMIT" in second["gate_reasons"]
    latest = store.recent_recommendations(limit=1)[0]["payload"]
    assert latest["daily_loss_pct"] == pytest.approx(10.0)


def test_bar_age_is_measured_in_utc_not_broker_clock(
    tmp_path: Path, no_notify: Callable[..., None]
) -> None:
    """The broker stamps bars on its own clock (GMT+3 here); the age must correct for it."""
    store = Store(tmp_path / "mt5.db")
    backend = FakeMT5(now=WEDNESDAY_10_UTC, symbol_name="USDJPY")
    settings = _settings(tmp_path)
    adapter = ReadOnlyMT5(backend)
    now = int(WEDNESDAY_10_UTC.timestamp())
    summary = watch._journal_and_decide(
        store, settings, watch._read_state(adapter, settings.symbol_root), now
    )

    # Subtracting server time raw would report -10,800 s, which no threshold can ever catch.
    # One closed M1 bar behind the last boundary is the freshest a real feed can be.
    assert summary["bar_age_s"] == 60
    assert "DATA_STALE" not in summary["gate_reasons"]
    assert "DATA_STALE_OR_UNVERIFIED" not in summary["gate_reasons"]
    assert store.recent_recommendations(limit=1)[0]["bar_epoch"] == now - (now % 60) - 60


def test_stale_feed_can_actually_trip_the_freshness_check(
    tmp_path: Path, no_notify: Callable[..., None]
) -> None:
    """A feed ten minutes behind 'now' must produce DATA_STALE; the check has to be able to fire."""
    store = Store(tmp_path / "mt5.db")
    backend = FakeMT5(now=WEDNESDAY_10_UTC, symbol_name="USDJPY")
    settings = _settings(tmp_path)
    adapter = ReadOnlyMT5(backend)
    now = int(WEDNESDAY_10_UTC.timestamp())
    # The terminal's bars stop ten minutes before the cycle's own clock.
    backend.now_epoch = now - 600
    state = watch._read_state(adapter, settings.symbol_root)
    backend.now_epoch = now

    summary = watch._journal_and_decide(store, settings, state, now)
    assert summary["bar_age_s"] == pytest.approx(600, abs=60)
    assert "DATA_STALE" in summary["gate_reasons"]


def test_missing_tick_leaves_clock_dependent_inputs_unverified(
    tmp_path: Path, no_notify: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No tick means no offset, so age, spread and the day's loss all default-deny."""
    store = Store(tmp_path / "mt5.db")
    backend = FakeMT5(now=WEDNESDAY_10_UTC, symbol_name="USDJPY")
    monkeypatch.setattr(backend, "symbol_info_tick", lambda name: None)
    settings = _settings(tmp_path)
    adapter = ReadOnlyMT5(backend)
    assert watch._broker_offset_s(settings, {"tick": None}, 0) is None

    summary = watch._journal_and_decide(
        store,
        settings,
        watch._read_state(adapter, settings.symbol_root),
        int(WEDNESDAY_10_UTC.timestamp()),
    )
    assert summary["bar_age_s"] is None
    assert "DATA_STALE_OR_UNVERIFIED" in summary["gate_reasons"]
    assert "SPREAD_UNVERIFIED" in summary["gate_reasons"]
    assert "DAILY_LOSS_UNVERIFIED" in summary["gate_reasons"]
    # Nothing derived from a clock that could not be resolved is journaled.
    assert store.recent_events("market_scan") == []


def test_stale_tick_offset_is_unverified_rather_than_a_wrong_day(
    tmp_path: Path, no_notify: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A terminal that has just logged in can still serve the last tick it held before it did.

    After a weekend that tick is days old, and (now - 4 days) is not a server offset: trusting it
    keys the broker day to the wrong date and files real equity under it, which is what a fresh
    demo login did - the demo's opening balance landed on a day four days in the past.
    """
    store = Store(tmp_path / "mt5.db")
    backend = FakeMT5(now=WEDNESDAY_10_UTC, symbol_name="USDJPY")
    settings = _settings(tmp_path)
    now = int(WEDNESDAY_10_UTC.timestamp())
    stale = int((WEDNESDAY_10_UTC - timedelta(days=4)).timestamp())
    monkeypatch.setattr(
        backend, "symbol_info_tick", lambda name: SimpleNamespace(time=stale, bid=150.1, ask=150.2)
    )

    assert watch._broker_offset_s(settings, {"tick": backend.symbol_info_tick("USDJPY")}, now) is None

    summary = watch._journal_and_decide(
        store, settings, watch._read_state(ReadOnlyMT5(backend), settings.symbol_root), now
    )
    # A clock that could not be resolved leaves everything derived from it unverified, and
    # nothing is filed against an invented day.
    assert summary["bar_age_s"] is None
    assert "DATA_STALE_OR_UNVERIFIED" in summary["gate_reasons"]
    assert "DAILY_LOSS_UNVERIFIED" in summary["gate_reasons"]
    assert store.equity_baseline((WEDNESDAY_10_UTC - timedelta(days=4)).date().isoformat()) is None


def test_daily_loss_baseline_ignores_another_accounts_equity(tmp_path: Path) -> None:
    """Switching account mid-day must not measure the new equity against the old account's.

    The journal outlives a login. Reading the previous account's opening equity would read as a
    95% loss on a fresh demo account, and the daily-loss breaker would trip on paper alone.
    """
    store = Store(tmp_path / "mt5.db")
    now = int(WEDNESDAY_10_UTC.timestamp())
    live = SimpleNamespace(login=49869754, equity=1000.0, balance=1000.0)
    demo = SimpleNamespace(login=49999999, equity=50.0, balance=50.0)

    assert watch._daily_loss_pct(store, live, now, 0) == 0.0
    # Same day, different account: its own first sighting, not the other account's numbers.
    assert watch._daily_loss_pct(store, demo, now, 0) == 0.0
    assert store.equity_baseline("2026-10-07", 49999999) is not None
    assert store.equity_baseline("2026-10-07", 49869754) is None

    # A genuine loss on the account that owns the day still counts.
    dropped = SimpleNamespace(login=49999999, equity=45.0, balance=50.0)
    assert watch._daily_loss_pct(store, dropped, now + 60, 0) == pytest.approx(10.0)


    store = Store(tmp_path / "mt5.db")
    now = int(WEDNESDAY_10_UTC.timestamp())
    assert watch._daily_loss_pct(store, None, now, 0) is None
    assert watch._daily_loss_pct(store, SimpleNamespace(equity=None, balance=1000.0), now, 0) is None
    # A cycle that could not read equity must not invent a baseline for the day.
    assert store.equity_baseline("2026-10-07") is None

    assert watch._daily_loss_pct(store, SimpleNamespace(equity=1000.0, balance=1000.0), now, 0) == 0.0
    assert store.equity_baseline("2026-10-07") is not None  # unreadable login: owner unknown


def test_daily_loss_baseline_follows_the_broker_day_not_utc(tmp_path: Path) -> None:
    """A GMT+3 broker rolls its day three hours before UTC does."""
    store = Store(tmp_path / "mt5.db")
    offset = 3 * 3600
    # 21:20 UTC on 7 Oct is already 00:20 on 8 Oct on a GMT+3 server.
    at_2120 = int(datetime(2026, 10, 7, 21, 20, tzinfo=UTC).timestamp())

    assert (
        watch._daily_loss_pct(store, SimpleNamespace(equity=1000.0, balance=1000.0), at_2120, offset)
        == 0.0
    )
    # The basis belongs to the broker's date, not UTC's date.
    assert store.equity_baseline("2026-10-08") is not None
    assert store.equity_baseline("2026-10-07") is None

    # Still the same broker day: the loss counts.
    dropped = SimpleNamespace(equity=900.0, balance=1000.0)
    assert watch._daily_loss_pct(store, dropped, at_2120 + 60, offset) == pytest.approx(10.0)

    # 21:00 UTC on 8 Oct is the broker's next midnight: the basis resets and the same
    # equity reads as flat again.
    at_next_midnight = int(datetime(2026, 10, 8, 21, 0, tzinfo=UTC).timestamp())
    assert watch._daily_loss_pct(store, dropped, at_next_midnight, offset) == 0.0
    assert store.equity_baseline("2026-10-09") is not None


def _h1_bars(n: int = 600) -> Bars:
    """A gentle wave with enough structure for the families to produce cards."""
    close = np.array([150.0 + 0.30 * float(np.sin(i / 11.0)) + 0.004 * i for i in range(n)])
    return Bars(
        time=np.arange(1_700_000_000, 1_700_000_000 + n * 3600, 3600, dtype=np.int64),
        open_=close,
        high=close + 0.02,
        low=close - 0.02,
        close=close,
        spread_points=np.full(n, 14, dtype=np.int32),
        point=0.001,
        digits=3,
        utc_offset_s=0,
    )


def _frame_bars(interval_s: int, n: int = 800) -> Bars:
    """A gentle wave on a faster frame, shaped the way `_h1_bars` shapes H1."""
    close = np.array([150.0 + 0.30 * float(np.sin(i / 11.0)) + 0.004 * i for i in range(n)])
    return Bars(
        time=np.arange(1_700_000_000, 1_700_000_000 + n * interval_s, interval_s, dtype=np.int64),
        open_=close,
        high=close + 0.02,
        low=close - 0.02,
        close=close,
        spread_points=np.full(n, 14, dtype=np.int32),
        point=0.001,
        digits=3,
        utc_offset_s=0,
    )


def test_setups_refresh_writes_the_live_view_on_its_own_timer(
    tmp_path: Path, no_notify: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live per-strategy artifact: written when stale, left alone when fresh."""
    artifact = tmp_path / "setups.json"
    monkeypatch.setattr(watch, "SETUPS_FILE", artifact)
    monkeypatch.setattr(watch, "load_bars", lambda *args, **kwargs: _h1_bars())
    store = Store(tmp_path / "mt5.db")
    backend = FakeMT5(now=WEDNESDAY_10_UTC, symbol_name="USDJPY")
    settings = _settings(tmp_path)
    adapter = ReadOnlyMT5(backend)
    # The refresh window is measured against the artifact's own mtime, so this clock has to be
    # the real one - a fixed test date would sit years away from the file just written. The fake
    # terminal has to agree with it, because its tick and bars are generated from `now_epoch`: a
    # fake dated five days earlier is a stale feed, and a stale tick yields no usable offset.
    now = int(time.time())
    backend.now_epoch = now
    state = watch._read_state(adapter, settings.symbol_root)

    watch._refresh_setups(adapter, settings, state, store, now)
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["timeframe"] == "H1"
    assert payload["symbol"] == "USDJPY"
    assert payload["setups"], "every family must appear, even one with no card"
    assert {"family", "status", "side", "entry", "sl", "tp"} <= set(payload["setups"][0])
    # The tick in the fake backend is GMT+3, so the bar labels are reported in UTC.
    assert payload["clock"] == "utc"

    # Inside the refresh window the artifact is left alone, however much the bars moved.
    artifact.write_text('{"sentinel": true}', encoding="utf-8")
    watch._refresh_setups(adapter, settings, state, store, now + 10)
    assert json.loads(artifact.read_text(encoding="utf-8")) == {"sentinel": True}

    watch._refresh_setups(adapter, settings, state, store, now + watch.SETUPS_REFRESH_S + 1)
    assert json.loads(artifact.read_text(encoding="utf-8"))["setups"]


def test_setups_refresh_failure_is_journaled_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reporting artifact must never be able to stop the watch loop."""
    store = Store(tmp_path / "mt5.db")
    monkeypatch.setattr(watch, "SETUPS_FILE", tmp_path / "setups.json")

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("terminal gone")

    monkeypatch.setattr(watch, "load_bars", boom)
    backend = FakeMT5(now=WEDNESDAY_10_UTC, symbol_name="USDJPY")
    settings = _settings(tmp_path)
    adapter = ReadOnlyMT5(backend)
    watch._refresh_setups(
        adapter, settings, watch._read_state(adapter, settings.symbol_root), store,
        int(WEDNESDAY_10_UTC.timestamp()),
    )
    errors = store.recent_events("setups_error")
    assert len(errors) == 1
    assert "terminal gone" in errors[0]["payload"]["message"]
    assert not (tmp_path / "setups.json").exists()


def test_extra_timeframes_write_their_own_frames_off_the_traded_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every watched symbol gets an M30 and an M15 card view, as display-only artifacts.

    The trading path reads `setups.json` and the per-symbol copies; the informational frames must
    never land on those names, or a faster frame's card inherits an authority its frame has not
    been validated on (GAP-9).
    """
    store = Store(tmp_path / "mt5.db")
    monkeypatch.setattr(watch, "SETUPS_FILE", tmp_path / "setups.json")
    # The fake terminal serves three days of history - 288 M15 bars, under the 500-bar floor
    # load_bars accepts - so the loader is fed frame-shaped bars and these tests assert the
    # artifact wiring: which names get written, for which symbol and frame, and never the
    # traded names. The bar-to-card pipeline itself is covered by test_setups and the H1
    # refresh test above.
    monkeypatch.setattr(
        watch,
        "load_bars",
        lambda *_args, **_kwargs: _frame_bars(900),
    )
    settings = replace(_settings(tmp_path), symbols=("EURUSD",))
    backend = FakeMT5(now=WEDNESDAY_10_UTC, symbol_name="USDJPY")
    adapter = ReadOnlyMT5(backend)
    watch._refresh_extra_timeframes(
        adapter, settings, store, int(WEDNESDAY_10_UTC.timestamp())
    )
    for symbol in ("USDJPY", "EURUSD"):
        for timeframe in ("M30", "M15"):
            artifact = tmp_path / f"setups_{symbol}_{timeframe}.json"
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            assert payload["timeframe"] == timeframe
            assert payload["symbol"] == symbol
            assert payload["setups"], "every family must appear, even one with no card"
    # Nothing the trading path reads was written by the informational refresh.
    assert not (tmp_path / "setups.json").exists()
    assert not (tmp_path / "setups_EURUSD.json").exists()
    assert store.recent_events("setups_error") == []


def test_a_dead_frame_did_not_cost_the_others_their_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One symbol-timeframe pair failing journals it; every other pair still writes."""
    store = Store(tmp_path / "mt5.db")
    monkeypatch.setattr(watch, "SETUPS_FILE", tmp_path / "setups.json")
    settings = replace(_settings(tmp_path), symbols=("EURUSD",))
    backend = FakeMT5(now=WEDNESDAY_10_UTC, symbol_name="USDJPY")
    adapter = ReadOnlyMT5(backend)
    frame_bars = _frame_bars(900)

    def flaky(
        backend: FakeMT5, symbol: str, timeframe: str, count: int,
        *, utc_offset_s: int | None = None,
    ) -> Bars | None:
        if timeframe == "M15":
            raise RuntimeError("history request failed")
        return frame_bars

    monkeypatch.setattr(watch, "load_bars", flaky)
    watch._refresh_extra_timeframes(
        adapter, settings, store, int(WEDNESDAY_10_UTC.timestamp())
    )

    assert (tmp_path / "setups_USDJPY_M30.json").exists()
    assert (tmp_path / "setups_EURUSD_M30.json").exists()
    assert not (tmp_path / "setups_USDJPY_M15.json").exists()
    assert not (tmp_path / "setups_EURUSD_M15.json").exists()
    errors = store.recent_events("setups_error")
    assert len(errors) == 2  # one per watched symbol, not one stopping the loop
    for error in errors:
        assert error["payload"]["timeframe"] == "M15"
        assert "history request failed" in error["payload"]["message"]


def test_unreadable_positions_default_deny_instead_of_stopping_the_cycle(
    tmp_path: Path, no_notify: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    store = Store(tmp_path / "mt5.db")
    backend = FakeMT5(now=WEDNESDAY_10_UTC)

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("positions unavailable")

    monkeypatch.setattr(backend, "positions_get", boom)
    settings = _settings(tmp_path)
    adapter = ReadOnlyMT5(backend)
    state = watch._read_state(adapter, settings.symbol_root)
    assert state["positions"] is None

    summary = watch._journal_and_decide(store, settings, state, int(WEDNESDAY_10_UTC.timestamp()))
    assert "POSITIONS_UNVERIFIED" in summary["gate_reasons"]
    payload = store.recent_recommendations(limit=1)[0]["payload"]
    assert payload["positions"] == {"total": None, "symbol": None}


def test_trades_refresh_writes_the_journal_on_its_own_timer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The trade journal: what the account holds, and which cards were live when it was taken."""
    store = Store(tmp_path / "mt5.db")
    artifact = tmp_path / "trades.json"
    monkeypatch.setattr(watch, "TRADES_FILE", artifact)
    setups_file = tmp_path / "setups.json"
    monkeypatch.setattr(watch, "SETUPS_FILE", setups_file)
    setups_file.write_text(
        json.dumps(
            {
                "asof_bar_utc": "2026-10-13 09:00",
                "setups": [
                    {
                        "family": "sweep_reclaim",
                        "status": "open",
                        "side": "buy",
                        "entry": 150.1,
                        "sl": 149.9,
                        "tp": 150.5,
                    },
                    {"family": "ma_crossover", "status": "stop", "side": "sell"},
                ],
            }
        ),
        encoding="utf-8",
    )
    # The clock is the real one: the refresh window is measured against the artifact's own mtime,
    # and the broker offset is only resolvable from a tick that is actually fresh.
    now = int(time.time())
    backend = FakeMT5(
        now=datetime.now(UTC),
        symbol_name="USDJPY",
        positions=1,
        deals=[
            SimpleNamespace(
                position_id=9000,
                entry=0,
                type=0,
                price=150.100,
                volume=0.01,
                profit=0.0,
                swap=0.0,
                commission=-1.0,
                time=now,
                symbol="USDJPY",
            )
        ],
    )
    settings = _settings(tmp_path)
    adapter = ReadOnlyMT5(backend)
    state = watch._read_state(adapter, settings.symbol_root)

    watch._refresh_trades(adapter, settings, state, store, now)
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["open_count"] == 1
    row = payload["trades"][0]
    assert row["ticket"] == 9000
    assert row["status"] == "open"
    # The fake position's own profit plus the commission its entry deal carries.
    assert row["net"] == pytest.approx(-0.5 - 1.0)
    # The tick in the fake backend is GMT+3, so the trade's stamp is reported in UTC.
    assert payload["clock"] == "utc"
    assert payload["account"] == 12345678
    assert payload["currency"] == "USC"
    assert [entry["family"] for entry in row["context"]] == ["sweep_reclaim"]

    # Inside the refresh window the journal is left alone, however much the account moved.
    artifact.write_text('{"sentinel": true}', encoding="utf-8")
    watch._refresh_trades(adapter, settings, state, store, now + 10)
    assert json.loads(artifact.read_text(encoding="utf-8")) == {"sentinel": True}

    # Past it the journal is rebuilt, and the cards that were live are read from the setups view.
    # The rebuild is judged against the file's own mtime, not the clock passed in, so the
    # sentinel is explicitly back-dated: a slow write on the wall clock must not read as fresh.
    os.utime(artifact, (now - 120, now - 120))
    watch._refresh_trades(adapter, settings, state, store, now + watch.TRADES_REFRESH_S + 1)
    again = json.loads(artifact.read_text(encoding="utf-8"))
    assert [entry["family"] for entry in again["trades"][0]["context"]] == ["sweep_reclaim"]


def test_trades_refresh_failure_is_journaled_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A trade journal that cannot be read must never stop the watch loop."""
    store = Store(tmp_path / "mt5.db")
    monkeypatch.setattr(watch, "TRADES_FILE", tmp_path / "trades.json")
    backend = FakeMT5(now=datetime.now(UTC), symbol_name="USDJPY", positions=1)

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("history unavailable")

    monkeypatch.setattr(backend, "history_deals_get", boom)
    settings = _settings(tmp_path)
    adapter = ReadOnlyMT5(backend)
    watch._refresh_trades(
        adapter,
        settings,
        watch._read_state(adapter, settings.symbol_root),
        store,
        int(time.time()),
    )
    errors = store.recent_events("trades_error")
    assert len(errors) == 1
    assert "history unavailable" in errors[0]["payload"]["message"]
    assert not (tmp_path / "trades.json").exists()


# --- auto-trading wiring -------------------------------------------------------------------
# The execution rules have their own suite. What is tested here is the join between them and the
# cycle: that the cycle hands the auto step the market it actually measured, and that the step
# reaches the terminal the loop is already connected with rather than a second connection of its
# own. Both are the kind of wiring mistake that leaves everything "working" and silently trades
# nothing, which is why they are pinned rather than assumed.


def _auto_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    """Point the cycle's artifacts at the test's own directory.

    The module constants are real paths in the repository, and a test that wrote through them would
    overwrite the dashboard's live files - including the switch, which is the one file here that can
    change what the user's account does.
    """
    setups = tmp_path / "setups.json"
    artifact = tmp_path / "auto.json"
    switch = tmp_path / "auto-switch.json"
    monkeypatch.setattr(watch, "SETUPS_FILE", setups)
    monkeypatch.setattr(watch, "AUTO_FILE", artifact)
    monkeypatch.setattr(watch, "AUTO_SWITCH_FILE", switch)
    # The selection is part of the same contract, and for a sharper reason than the other three: the
    # dashboard writes a real one next to the repository while it is being used, and a selection file
    # wins over the .env list. A test that left this pointing at the repository would trade whatever
    # strategies the account holder last ticked instead of the ones the test set up, and fail on the
    # machine that actually uses the dashboard - which is what happened once a selection was saved.
    monkeypatch.setattr(watch, "AUTO_SELECTION_FILE", tmp_path / "auto-strategies.json")
    return setups, artifact, switch


def test_the_cycle_summary_carries_the_market_the_auto_step_needs(
    tmp_path: Path, no_notify: Callable[..., None]
) -> None:
    """The per-card gate is handed the cycle's own readings, not readings taken a second time.

    A card judged on a spread measured separately from the cycle's could be passed on a market the
    cycle itself never saw, so the two have to be the same numbers. This pins the contract rather
    than the values, which the market tests above already cover.
    """
    store = Store(tmp_path / "mt5.db")
    backend = FakeMT5(now=WEDNESDAY_10_UTC, symbol_name="USDJPY")
    settings = _settings(tmp_path)
    summary = watch._journal_and_decide(
        store,
        settings,
        watch._read_state(ReadOnlyMT5(backend), settings.symbol_root),
        int(WEDNESDAY_10_UTC.timestamp()),
    )
    market = summary["market"]
    for key in (
        "bar_age_s",
        "spread_pips",
        "market_open",
        "daily_loss_pct",
        "open_positions",
        "circuit_breaker_tripped",
        "news_verifiable",
        "news_excluded",
        "offset_s",
    ):
        assert key in market, key
    # The same spread the cycle journaled, not a second look at the tick.
    assert market["spread_pips"] == summary["spread_pips"]


def test_auto_step_writes_the_view_and_places_nothing_when_the_master_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = Store(tmp_path / "mt5.db")
    _, artifact, _ = _auto_paths(tmp_path, monkeypatch)
    backend = FakeMT5(now=WEDNESDAY_10_UTC, symbol_name="USDJPY")
    settings = _settings(tmp_path)  # auto_trade defaults to off
    watch._auto_step(
        backend,
        settings,
        store,
        {"market": {"bar_age_s": 5, "spread_pips": 1.4, "market_open": True, "offset_s": 3 * 3600}},
        int(WEDNESDAY_10_UTC.timestamp()),
    )
    assert backend.orders == []
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["switch"]["enabled"] is False
    assert payload["placed"] == []
    assert payload["notes"]  # it says why it is idle rather than showing nothing


def test_auto_step_trades_a_live_card_once_both_switches_are_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole join, end to end: registry, setups file, switch, sizing and the order request."""
    store = Store(tmp_path / "mt5.db")
    setups_file, artifact, switch_file = _auto_paths(tmp_path, monkeypatch)
    now = int(WEDNESDAY_10_UTC.timestamp())
    signal_epoch = now + 3 * 3600 - 3600
    setups_file.write_text(
        json.dumps(
            {
                "generated_utc": WEDNESDAY_10_UTC.isoformat(),
                "digits": 3,
                "max_hold_bars": 24,
                "setups": [
                    {
                        "family": "carry_trend",
                        "status": "open",
                        "side": "buy",
                        "signal_epoch": signal_epoch,
                        "signal_utc": "2026-10-07 10:00",
                        "entry": 150.100,
                        "sl": 149.800,
                        "tp": 150.700,
                        "stop_pips": 30.0,
                        "tp_r": 2.0,
                        "bars_held": 0,
                        "r_now": 0.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    switch_file.write_text(json.dumps({"enabled": True}), encoding="utf-8")
    store.set_strategy_status("carry_trend", "H1", "historically validated", {"note": "test"})
    settings = replace(
        _settings(tmp_path),
        auto_trade=True,
        auto_strategies=("carry_trend",),
        auto_risk_pct=1.0,
    )
    backend = FakeMT5(
        now=WEDNESDAY_10_UTC,
        symbol_name="USDJPY",
        advance_ticks=False,  # bid 150.100 / ask 150.114, so the card is at its entry
        equity=1000.0,
    )
    market = {
        "bar_age_s": 5,
        "spread_pips": 1.4,
        "market_open": True,
        "daily_loss_pct": 0.0,
        "open_positions": 0,
        "circuit_breaker_tripped": False,
        "news_verifiable": True,
        "news_excluded": False,
        "offset_s": 3 * 3600,
    }
    watch._auto_step(backend, settings, store, {"market": market}, now)

    assert len(backend.orders) == 1
    request = backend.orders[0]
    assert request["symbol"] == "USDJPY"
    assert request["sl"] == 149.800
    assert request["tp"] == 150.700
    assert request["magic"] == settings.auto_magic
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["switch"]["enabled"] is True
    assert payload["allow"]["allowed"] == ["carry_trend"]
    assert payload["placed"][0]["strategy"] == "carry_trend"
    assert payload["open_count"] == 1

    # A second cycle on the same card is not a second position.
    watch._auto_step(backend, settings, store, {"market": market}, now + 30)
    assert len(backend.orders) == 1


def test_auto_step_trades_an_extra_symbol_from_its_own_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second watched market trades from its own view, not the primary's.

    The extra symbols sat out every cycle once - the loop built their views *after* the auto step
    had already run, so every EURUSD card met `no per-symbol market reading yet this cycle` while
    the primary went on trading alone. A view gathered in time is enough: the card is read and
    the order carries that market's own symbol.
    """
    store = Store(tmp_path / "mt5.db")
    _setups_file, artifact, switch_file = _auto_paths(tmp_path, monkeypatch)
    now = int(WEDNESDAY_10_UTC.timestamp())
    signal_epoch = now + 3 * 3600 - 3600
    # Only EURUSD has a card; the primary's own setups file is deliberately absent.
    watch._setups_extra_path("EURUSD").write_text(
        json.dumps(
            {
                "generated_utc": WEDNESDAY_10_UTC.isoformat(),
                "digits": 3,
                "max_hold_bars": 24,
                "setups": [
                    {
                        "family": "carry_trend",
                        "status": "open",
                        "side": "buy",
                        "signal_epoch": signal_epoch,
                        "signal_utc": "2026-10-07 10:00",
                        "entry": 150.100,
                        "sl": 149.800,
                        "tp": 150.700,
                        "stop_pips": 30.0,
                        "tp_r": 2.0,
                        "bars_held": 0,
                        "r_now": 0.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    switch_file.write_text(json.dumps({"enabled": True}), encoding="utf-8")
    store.set_strategy_status("carry_trend", "H1", "historically validated", {"note": "test"})
    settings = replace(
        _settings(tmp_path),
        symbols=("EURUSD",),
        auto_trade=True,
        auto_strategies=("carry_trend",),
        auto_risk_pct=1.0,
    )
    backend = FakeMT5(now=WEDNESDAY_10_UTC, symbol_name="USDJPY", advance_ticks=False)
    summary = {
        "market": {
            "bar_age_s": 5,
            "spread_pips": 1.4,
            "market_open": True,
            "daily_loss_pct": 0.0,
            # The account holds three positions, one of them the engine's. The extra symbol's
            # gate must budget against the engine's, exactly as the primary's does.
            "open_positions": 3,
            "auto_positions": 1,
            "circuit_breaker_tripped": False,
            # The primary's own readings, the way the loop's summary carries them.
            "news_verifiable": True,
            "news_excluded": False,
        },
        "symbols": {
            # What the loop must have gathered before this step: EURUSD's own fresh reading.
            # Deliberately without a news verdict: the view may be older than its market's
            # reading, and then the step borrows the primary's instead of default-deny.
            "EURUSD": {"available": True, "bar_age_s": 4, "spread_pips": 1.2},
        },
    }
    watch._auto_step(backend, settings, store, summary, now)

    assert len(backend.orders) == 1
    assert backend.orders[0]["symbol"] == "EURUSD"
    payload = json.loads((artifact.parent / "auto_EURUSD.json").read_text(encoding="utf-8"))
    assert payload["placed"][0]["strategy"] == "carry_trend"
    assert payload["allow"]["symbol"] == "EURUSD"
    # The file is this market's own: its own timestamp and its own readings - EURUSD's bar and
    # spread, not the primary's numbers beside it (bar 4 s / 1.2 pip vs USDJPY's 5 s / 1.4).
    assert "generated_utc" in payload
    assert payload["market"]["bar_age_s"] == 4
    assert payload["market"]["spread_pips"] == 1.2
    assert payload["market"]["news_verifiable"] is True  # borrowed from the primary's verdict
    # The cap means the same thing here as on the primary: one engine-held position, not the
    # account's three - which is what once starved every entry whenever you traded by hand.
    assert payload["market"]["open_positions"] == 1
    # The old failure mode was a skip event per cycle; it must not reappear.
    assert store.recent_events("auto_skipped") == []


def test_the_cap_reads_auto_made_positions_not_the_whole_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_notify: Callable[..., None]
) -> None:
    """Manual trades on the account must not eat the auto engine's own cap.

    Two manual trades and one auto trade sit on the account. Read as a whole that is 3 open at a
    cap of 2, so every card died on the gate's position limit even though the engine itself held
    one position and room for another - which is what starved all entries whenever the account
    holder traded by hand. The cycle therefore counts its own (magic-tagged) exposure for the
    cap, and the card goes through.
    """
    store = Store(tmp_path / "mt5.db")
    setups_file, artifact, switch_file = _auto_paths(tmp_path, monkeypatch)
    now = int(WEDNESDAY_10_UTC.timestamp())
    signal_epoch = now + 3 * 3600 - 3600
    setups_file.write_text(
        json.dumps(
            {
                "generated_utc": WEDNESDAY_10_UTC.isoformat(),
                "digits": 3,
                "max_hold_bars": 24,
                "setups": [
                    {
                        "family": "carry_trend",
                        "status": "open",
                        "side": "buy",
                        "signal_epoch": signal_epoch,
                        "signal_utc": "2026-10-07 10:00",
                        "entry": 150.100,
                        "sl": 149.800,
                        "tp": 150.700,
                        "stop_pips": 30.0,
                        "tp_r": 2.0,
                        "bars_held": 0,
                        "r_now": 0.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    switch_file.write_text(json.dumps({"enabled": True}), encoding="utf-8")
    store.set_strategy_status("carry_trend", "H1", "historically validated", {"note": "test"})
    settings = replace(
        _settings(tmp_path),
        auto_trade=True,
        auto_strategies=("carry_trend",),
        auto_risk_pct=1.0,
    )
    backend = FakeMT5(
        now=WEDNESDAY_10_UTC,
        symbol_name="USDJPY",
        advance_ticks=False,  # bid 150.100 / ask 150.114, so the card is at its entry
        equity=1000.0,
        positions=2,  # the account holder's own manual trades
    )
    auto_row = SimpleNamespace(
        ticket=777001,
        symbol="USDJPY",
        type=0,
        volume=0.01,
        price_open=150.100,
        price_current=150.100,
        sl=0.0,
        tp=0.0,
        profit=0.0,
        swap=0.0,
        time=now - 1200,
        magic=settings.auto_magic,
        comment="",
    )
    original_positions_get = backend.positions_get

    def positions_get(symbol: str | None = None) -> list[Any]:
        return [*original_positions_get(symbol), auto_row]

    monkeypatch.setattr(backend, "positions_get", positions_get)
    state = watch._read_state(ReadOnlyMT5(backend), settings.symbol_root)
    summary = watch._journal_and_decide(store, settings, state, now)
    market = summary["market"]
    assert market["open_positions"] == 3  # the account total is still the account total
    assert market["auto_positions"] == 1  # and the engine's own is read beside it

    watch._auto_step(backend, settings, store, summary, now)
    assert len(backend.orders) == 1
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    # What the gate saw was the engine's own count, not the account's 3.
    assert payload["market"]["open_positions"] == 1
    assert payload["placed"][0]["strategy"] == "carry_trend"


def test_auto_step_failure_is_journaled_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dead watcher cannot close anything, so nothing in the auto step may stop the loop."""
    store = Store(tmp_path / "mt5.db")
    _auto_paths(tmp_path, monkeypatch)
    backend = FakeMT5(now=WEDNESDAY_10_UTC, symbol_name="USDJPY")

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("the account will not read")

    monkeypatch.setattr(backend, "account_info", boom)
    settings = replace(_settings(tmp_path), auto_trade=True, auto_strategies=("carry_trend",))
    watch._auto_step(backend, settings, store, {"market": {}}, int(WEDNESDAY_10_UTC.timestamp()))
    # An account that will not read is a refusal inside the step, so the cycle is untouched...
    assert backend.orders == []
    payload = json.loads((tmp_path / "auto.json").read_text(encoding="utf-8"))
    assert payload["errors"] or payload["switch"]["enabled"] is False


def test_an_extra_symbol_with_its_own_reading_trades_rather_than_sits_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A watched non-primary symbol whose cycle produced readings runs its own auto step.

    The views used to be gathered after the step ran, so the map was empty at the moment it was
    read: every cycle filed "no per-symbol market reading yet this cycle" and EURUSD never traded
    at all. The step now reads the map the same cycle built it.
    """
    store = Store(tmp_path / "mt5.db")
    setups_file, _artifact, switch_file = _auto_paths(tmp_path, monkeypatch)
    now = int(WEDNESDAY_10_UTC.timestamp())
    signal_epoch = now + 3 * 3600 - 3600
    setups_extra = tmp_path / "setups_EURUSD.json"
    monkeypatch.setattr(watch, "SETUPS_FILE", setups_file.parent / "setups_USDJPY.json")
    setups_extra.write_text(
        json.dumps(
            {
                "generated_utc": WEDNESDAY_10_UTC.isoformat(),
                "digits": 5,
                "max_hold_bars": 24,
                "setups": [
                    {
                        "family": "sweep_reclaim",
                        "status": "open",
                        "side": "buy",
                        "signal_epoch": signal_epoch,
                        "signal_utc": "2026-10-07 10:00",
                        "entry": 1.12100,
                        "sl": 1.11800,
                        "tp": 1.12700,
                        "stop_pips": 30.0,
                        "tp_r": 2.0,
                        "bars_held": 0,
                        "r_now": 0.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    switch_file.write_text(json.dumps({"enabled": True}), encoding="utf-8")
    store.set_strategy_status("sweep_reclaim", "H1", "historically validated", {"note": "test"})
    settings = replace(
        _settings(tmp_path),
        auto_trade=True,
        auto_strategies=("sweep_reclaim",),
        symbols=("EURUSD",),
    )
    backend = FakeMT5(
        now=WEDNESDAY_10_UTC,
        symbol_name="EURUSD",
        advance_ticks=False,  # bid 1.12100 / ask 1.12114, so the card is at its entry
        tick_bid=1.12100,
        tick_ask=1.12114,
        equity=1000.0,
    )
    market = {
        "bar_age_s": 5,
        "spread_pips": 1.4,
        "market_open": True,
        "daily_loss_pct": 0.0,
        "open_positions": 0,
        "circuit_breaker_tripped": False,
        "news_verifiable": True,
        "news_excluded": False,
        "offset_s": 3 * 3600,
    }
    summary = {"market": market, "symbols": {"EURUSD": {"available": True, "bar_age_s": 5, "spread_pips": 1.7}}}
    watch._auto_step(backend, settings, store, summary, now)

    assert store.recent_events("auto_skipped") == []
    assert len(backend.orders) == 1
    assert backend.orders[0]["symbol"] == "EURUSD"


def test_the_auto_gate_counts_what_the_engine_holds_not_what_the_account_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Manual exposure does not consume the machine's budget.

    The account may hold the holder's own trades alongside the engine's, which is exactly what made
    every fresh card answer "position limit" while he traded in parallel. When the cycle measures
    both counts, the engine's gate is given the engine's; an account total it cannot decompose
    (auto_positions None) still refuses, so an unreadable count never passes by default.
    """
    market = {
        "bar_age_s": 5,
        "spread_pips": 1.4,
        "market_open": True,
        "daily_loss_pct": 0.0,
        "open_positions": 5,
        "auto_positions": 0,
        "circuit_breaker_tripped": False,
        "news_verifiable": True,
        "news_excluded": False,
        "offset_s": 3 * 3600,
    }
    store = Store(tmp_path / "mt5.db")
    setups_file, _artifact, switch_file = _auto_paths(tmp_path, monkeypatch)
    now = int(WEDNESDAY_10_UTC.timestamp())
    signal_epoch = now + 3 * 3600 - 3600
    setups_file.write_text(
        json.dumps(
            {
                "generated_utc": WEDNESDAY_10_UTC.isoformat(),
                "digits": 3,
                "max_hold_bars": 24,
                "setups": [
                    {
                        "family": "carry_trend",
                        "status": "open",
                        "side": "buy",
                        "signal_epoch": signal_epoch,
                        "signal_utc": "2026-10-07 10:00",
                        "entry": 150.100,
                        "sl": 149.800,
                        "tp": 150.700,
                        "stop_pips": 30.0,
                        "tp_r": 2.0,
                        "bars_held": 0,
                        "r_now": 0.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    switch_file.write_text(json.dumps({"enabled": True}), encoding="utf-8")
    store.set_strategy_status("carry_trend", "H1", "historically validated", {"note": "test"})
    settings = replace(
        _settings(tmp_path), auto_trade=True, auto_strategies=("carry_trend",)
    )
    backend = FakeMT5(
        now=WEDNESDAY_10_UTC,
        symbol_name="USDJPY",
        advance_ticks=False,  # bid 150.100 / ask 150.114, at the entry
        equity=1000.0,
    )

    # Five positions on the account, all manual: the engine still has its whole budget.
    watch._auto_step(backend, settings, store, {"market": market}, now)
    assert len(backend.orders) == 1, "the manual trades must not eat the machine's cap"

    # Without a decomposition the count cannot be trusted, so the old refusal stands.
    backend = FakeMT5(now=WEDNESDAY_10_UTC, symbol_name="USDJPY", equity=1000.0)
    unverified = dict(market, auto_positions=None, open_positions=settings.auto_max_positions)
    watch._auto_step(backend, settings, store, {"market": unverified}, now)
    assert backend.orders == []
