"""Position management: what the watcher does with a trade AFTER it was placed.

Every test here starts from the same seeded trade the exit tests use - one live long on
USDJPY, one stop 30 pips below its entry, one target 2R above - and asks whether the cycle
moved its exits the way the management rules say it must. The fake's tick prices are pinned
per test, so `profit_r` is exact arithmetic: 0.30 of distance is 1R, and every threshold in
the settings is readable off the price the test chose.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fakes import FakeMT5
from mt5_intel.execution import artifact
from mt5_intel.store import Store
from test_execution import (
    NOW,
    _arm,
    _backend,
    _card_row,
    _market,
    _payload,
    _run,
    _seed_open,
    _settings,
)

pytestmark = pytest.mark.usefixtures("tmp_path")


def _pin_tick(backend: FakeMT5, bid: float, ask: float) -> None:
    """Pin every tick read this run makes to a price the test chose."""
    epoch = backend.now_epoch

    def tick(name: str) -> Any:
        return SimpleNamespace(time=epoch + 3 * 3600 - 2, time_msc=(epoch + 3 * 3600) * 1000, bid=bid, ask=ask)

    backend.symbol_info_tick = tick  # type: ignore[method-assign]


def _sltp_requests(backend: FakeMT5) -> list[dict[str, Any]]:
    return [row for row in backend.orders if row.get("action") == FakeMT5.TRADE_ACTION_SLTP]


def _deal_requests(backend: FakeMT5) -> list[dict[str, Any]]:
    return [row for row in backend.orders if row.get("action") == FakeMT5.TRADE_ACTION_DEAL]


def _managed_events(store: Store, kind: str) -> list[dict[str, Any]]:
    """The journal's `auto_managed` payloads of a given kind (the rows nest their payload)."""
    return [row["payload"] for row in store.recent_events("auto_managed") if row["payload"].get("kind") == kind]


def _live(backend: FakeMT5, store: Store, *, ticket: int = 9001, **overrides: Any) -> int:
    """The seeded trade, plus the live position the terminal is holding behind it."""
    row_id = _seed_open(store, backend, ticket=ticket, **overrides)
    backend.add_position(
        ticket,
        symbol="USDJPY",
        price_open=float(overrides.get("entry", 150.100)),
        sl=float(overrides.get("sl", 149.800)),
        tp=float(overrides.get("tp", 150.700)),
    )
    return row_id


# --- stop management ---------------------------------------------------------------------


def test_a_trade_1r_in_profit_has_its_stop_locked_past_entry(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    _live(backend, store)
    _pin_tick(backend, bid=150.400, ask=150.414)  # entry + 1R exactly

    _run(backend, store, _settings(), setups=_payload([]), switch_path=_arm(tmp_path))

    moved = _sltp_requests(backend)
    assert len(moved) == 1
    assert moved[0]["sl"] == pytest.approx(150.130)  # lock = 0.1R above entry
    assert moved[0]["tp"] == pytest.approx(150.700)  # the target is untouched
    # The journal now reads the stop the broker holds.
    assert store.auto_trades_open("USDJPY")[0]["sl"] == pytest.approx(150.130)
    events = _managed_events(store, "breakeven")
    assert len(events) == 1


def test_profit_below_the_threshold_never_moves_the_stop(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    _live(backend, store)
    _pin_tick(backend, bid=150.200, ask=150.214)  # 0.33R: nothing is owed yet

    _run(backend, store, _settings(), setups=_payload([]), switch_path=_arm(tmp_path))

    assert _sltp_requests(backend) == []
    assert store.auto_trades_open("USDJPY")[0]["sl"] == pytest.approx(149.800)


def test_the_stop_is_never_moved_back_down(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    _live(backend, store)

    _pin_tick(backend, bid=150.400, ask=150.414)
    _run(backend, store, _settings(), setups=_payload([]), switch_path=_arm(tmp_path))
    # 1.33R: a breakeven candidate exists, but it is not better than the stop already held,
    # so this cycle sends nothing. 0.33R later: no candidate at all. Either way: no touch.
    _pin_tick(backend, bid=150.500, ask=150.514)
    _run(backend, store, _settings(), setups=_payload([]), switch_path=_arm(tmp_path))
    _pin_tick(backend, bid=150.200, ask=150.214)
    _run(backend, store, _settings(), setups=_payload([]), switch_path=_arm(tmp_path))

    assert len(_sltp_requests(backend)) == 1
    assert store.auto_trades_open("USDJPY")[0]["sl"] == pytest.approx(150.130)


def test_trailing_keeps_one_r_behind_price_and_only_ever_rises(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    _live(backend, store)

    # 2.5R: the trail is the better rule, and it wins over the breakeven lock.
    _pin_tick(backend, bid=150.850, ask=150.864)
    _run(backend, store, _settings(), setups=_payload([]), switch_path=_arm(tmp_path))
    assert len(_sltp_requests(backend)) == 1
    assert store.auto_trades_open("USDJPY")[0]["sl"] == pytest.approx(150.550)  # mark - 1R

    # The price coming back cannot drag the stop back down with it.
    # Well came off the exact 2.0R edge: at 2.2R the trail rule fires, but its candidate
    # (mark - 1R = 150.460) is worse than the stop already held, so nothing is sent.
    _pin_tick(backend, bid=150.760, ask=150.774)
    _run(backend, store, _settings(), setups=_payload([]), switch_path=_arm(tmp_path))
    assert len(_sltp_requests(backend)) == 1
    events = _managed_events(store, "trail")
    assert len(events) == 1


def test_a_stop_would_not_be_placed_past_the_target(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    _live(backend, store)
    # 3.17R in profit: the trail candidate (mark - 1R = 150.750) sits on the far side of the
    # target the broker holds, so the move is refused by design - the broker will close this
    # trade at the target within the tick, and a stop there would only be rejected anyway.
    _pin_tick(backend, bid=151.050, ask=151.064)
    _run(backend, store, _settings(), setups=_payload([]), switch_path=_arm(tmp_path))

    assert _sltp_requests(backend) == []


def test_a_refused_stop_motion_is_journaled_and_the_row_reads_the_old_stop(tmp_path: Path) -> None:
    backend = _backend(order_retcode=10016, order_comment="Invalid stops")
    store = Store(tmp_path / "m.db")
    _live(backend, store)
    _pin_tick(backend, bid=150.400, ask=150.414)

    outcome = _run(backend, store, _settings(), setups=_payload([]), switch_path=_arm(tmp_path))

    assert store.auto_trades_open("USDJPY")[0]["sl"] == pytest.approx(149.800)
    assert any("stop move" in item for item in outcome.errors)
    assert [row for row in store.recent_events("auto_manage_failed")]


def test_management_off_leaves_the_brokers_stops_completely_alone(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    _live(backend, store)
    _pin_tick(backend, bid=150.400, ask=150.414)

    _run(backend, store, _settings(auto_manage=False), setups=_payload([]), switch_path=_arm(tmp_path))

    assert backend.orders == []


# --- what a newer signal does to a live trade -----------------------------------------------


def test_a_newer_same_side_card_hands_its_target_over_and_never_opens_a_second(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    _live(backend, store)
    _pin_tick(backend, bid=150.100, ask=150.114)
    card = _card_row("carry_trend", epoch=NOW - 3 * 3600 - 3600, entry=150.400, sl=150.100, tp=150.900)

    outcome = _run(backend, store, _settings(), setups=_payload([card]), switch_path=_arm(tmp_path))

    deals = _deal_requests(backend)
    assert deals == []  # no second copy of the trade: the live one is the position that counts
    moved = _sltp_requests(backend)
    assert len(moved) == 1 and moved[0]["tp"] == pytest.approx(150.900)
    row = store.auto_trades_open("USDJPY")[0]
    assert row["tp"] == pytest.approx(150.900)
    assert [row for row in outcome.managed if row.get("kind") == "tp_handoff"]


def test_an_older_same_side_card_changes_nothing(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    _live(backend, store, signal_epoch=NOW - 3 * 3600 - 1800)
    _pin_tick(backend, bid=150.100, ask=150.114)
    card = _card_row("carry_trend", epoch=NOW - 3 * 3600 - 7200, entry=150.400, sl=150.100, tp=150.900)

    _run(backend, store, _settings(), setups=_payload([card]), switch_path=_arm(tmp_path))

    assert backend.orders == []
    assert store.auto_trades_open("USDJPY")[0]["tp"] == pytest.approx(150.700)


def test_an_opposite_card_closes_the_live_trade_and_trades_the_reversal(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    _live(backend, store)
    _pin_tick(backend, bid=150.380, ask=150.394)
    card = _card_row(
        "carry_trend",
        side="sell",
        epoch=NOW - 3 * 3600 - 3600,
        entry=150.400,
        sl=150.700,
        tp=149.800,
    )

    outcome = _run(backend, store, _settings(), setups=_payload([card]), switch_path=_arm(tmp_path))

    deals = _deal_requests(backend)
    assert len(deals) == 2
    # The first deal is the close of the held trade - the opposite side, naming its ticket.
    assert deals[0]["position"] == 9001 and deals[0]["type"] == FakeMT5.ORDER_TYPE_SELL
    # The second is the reversal, on its own ticket with the card's own exits.
    assert "position" not in deals[1] and deals[1]["tp"] == pytest.approx(149.800)
    rows = store.recent_auto_trades()
    closed = [row for row in rows if row["status"] == "closed"]
    assert [row["close_reason"] for row in closed] == ["opposite signal"]
    assert [row["side"] for row in outcome.placed] == ["sell"]


def test_reverse_close_off_keeps_the_cap_semantics_and_the_live_trade(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    _live(backend, store)
    _pin_tick(backend, bid=150.380, ask=150.394)
    card = _card_row(
        "carry_trend",
        side="sell",
        epoch=NOW - 3 * 3600 - 3600,
        entry=150.400,
        sl=150.700,
        tp=149.800,
    )
    settings = _settings(auto_reverse_close=False, auto_max_positions=1)

    outcome = _run(backend, store, settings, setups=_payload([card]), switch_path=_arm(tmp_path))

    assert backend.orders == []  # nothing closes nothing
    assert any("cap" in item["reason"] for item in outcome.refused)
    assert store.auto_trades_open("USDJPY")[0]["status"] == "placed"


def test_a_stale_opposite_card_closes_nothing(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    _live(backend, store)
    # Price has already run below the card's own plan: by the entry-freshness rule this signal
    # has gone, so it may close nothing and it may not be traded either.
    _pin_tick(backend, bid=150.100, ask=150.114)
    card = _card_row(
        "carry_trend",
        side="sell",
        epoch=NOW - 3 * 3600 - 3600,
        entry=150.400,
        sl=150.700,
        tp=149.800,
    )

    outcome = _run(backend, store, _settings(), setups=_payload([card]), switch_path=_arm(tmp_path))

    assert backend.orders == []
    assert store.auto_trades_open("USDJPY")[0]["status"] == "placed"
    assert outcome.placed == []


# --- what the dashboard reads ----------------------------------------------------------------


def test_the_artifact_carries_the_managed_actions(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    _live(backend, store)
    _pin_tick(backend, bid=150.400, ask=150.414)
    settings = _settings()

    outcome = _run(backend, store, settings, setups=_payload([]), switch_path=_arm(tmp_path))
    view = artifact(outcome, settings, market=_market(), now_epoch=NOW)

    assert [item["kind"] for item in view["managed"]] == ["breakeven"]
    assert view["managed"][0]["sl"] == pytest.approx(150.130)


# --- the backtest engine has to measure the same management -----------------------------
#
# The freqtrade engine grades these families, and a backtest with fixed barriers grades a
# system that does not trade: the live account locks and trails, so the losses a fixed-barrier
# backtest shows are not the losses the account would take. These tests hold the two together.


def _tools_on_path() -> None:
    tools = Path(__file__).resolve().parents[1] / "tools"
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))


def test_the_runner_hands_over_the_settings_the_watcher_actually_reads(monkeypatch) -> None:
    """`manage_env` is the whole bridge, so it must follow the .env, not a copy of it."""
    _tools_on_path()
    from freqtrade_run_backtest import manage_env

    monkeypatch.setenv("AUTO_MANAGE", "yes")
    monkeypatch.setenv("AUTO_BREAKEVEN_R", "1.25")
    monkeypatch.setenv("AUTO_LOCK_R", "0.2")
    monkeypatch.setenv("AUTO_TRAIL_R", "2.5")
    monkeypatch.setenv("AUTO_TRAIL_DISTANCE_R", "0.75")

    handed = manage_env()

    assert handed["MT5INTEL_MANAGE"] == "1"
    assert float(handed["MT5INTEL_BREAKEVEN_R"]) == pytest.approx(1.25)
    assert float(handed["MT5INTEL_LOCK_R"]) == pytest.approx(0.2)
    assert float(handed["MT5INTEL_TRAIL_R"]) == pytest.approx(2.5)
    assert float(handed["MT5INTEL_TRAIL_DISTANCE_R"]) == pytest.approx(0.75)

    # The switch is a real switch: off, and the backtest measures fixed barriers again.
    monkeypatch.setenv("AUTO_MANAGE", "off")
    assert manage_env()["MT5INTEL_MANAGE"] == "0"


def test_the_strategy_reads_exactly_the_variables_the_runner_writes(monkeypatch) -> None:
    """Both halves of the bridge, checked against each other by name.

    The freqtrade venv has none of this project's dependencies, so `MT5Family.py` learns where
    the watcher puts a stop from environment variables or not at all. A typo on either side
    leaves the backtest silently grading fixed barriers while the live account trails - and the
    difference would read as an edge that does not exist. The runner's side is imported and run;
    the strategy's side is read off its source, because importing it needs freqtrade.
    """
    _tools_on_path()
    from freqtrade_run_backtest import PROJECT_ROOT, manage_env

    monkeypatch.delenv("AUTO_MANAGE", raising=False)
    for name in ("AUTO_BREAKEVEN_R", "AUTO_LOCK_R", "AUTO_TRAIL_R", "AUTO_TRAIL_DISTANCE_R"):
        monkeypatch.delenv(name, raising=False)
    handed = manage_env()

    source = (PROJECT_ROOT / "user_data" / "strategies" / "MT5Family.py").read_text(
        encoding="utf-8"
    )
    read = set(re.findall(r'"(MT5INTEL_[A-Z_]+)"', source))

    assert read == set(handed), (
        "the strategy and the runner disagree about the channel: "
        f"strategy reads {sorted(read)}, runner writes {sorted(handed)}"
    )
    # And the defaults agree when no .env is in play, so a strategy loaded outside the runner
    # is not quietly running a different set of rules.
    assert set(read) == {
        "MT5INTEL_MANAGE",
        "MT5INTEL_BREAKEVEN_R",
        "MT5INTEL_LOCK_R",
        "MT5INTEL_TRAIL_R",
        "MT5INTEL_TRAIL_DISTANCE_R",
    }


def test_the_shared_reader_is_the_one_load_settings_uses(monkeypatch) -> None:
    """`Settings` and the freqtrade bridge must read one source, not two that can drift.

    `load_settings` builds its five stop-management fields from `load_stop_manage`, so the
    watcher and the backtest cannot be pointed at different numbers by editing one of them.
    """
    from mt5_intel.config import load_settings, load_stop_manage

    # Pinned rather than cleared: `load_stop_manage` reads the process environment, which an
    # earlier test - or a real .env - may already have filled in.
    monkeypatch.setenv("AUTO_MANAGE", "1")
    monkeypatch.setenv("AUTO_BREAKEVEN_R", "1.0")
    monkeypatch.setenv("AUTO_LOCK_R", "0.1")
    monkeypatch.setenv("AUTO_TRAIL_R", "2.0")
    monkeypatch.setenv("AUTO_TRAIL_DISTANCE_R", "1.0")
    stop = load_stop_manage()
    source = Path(load_settings.__code__.co_filename).read_text(encoding="utf-8")

    assert "stop = load_stop_manage()" in source
    for field in (
        "auto_manage=stop.manage",
        "auto_breakeven_r=stop.breakeven_r",
        "auto_lock_r=stop.lock_r",
        "auto_trail_r=stop.trail_r",
        "auto_trail_distance_r=stop.trail_distance_r",
    ):
        assert field in source, f"load_settings no longer takes {field.split('=')[0]} from load_stop_manage"
    # The defaults are the documented ones, so an untouched .env is the documented behaviour.
    assert (stop.manage, stop.breakeven_r, stop.lock_r, stop.trail_r, stop.trail_distance_r) == (
        True,
        1.0,
        0.1,
        2.0,
        1.0,
    )
