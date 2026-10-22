"""Auto-trading, tested at the seams where it can cost money.

The interesting cases here are the ones where it must *not* trade: a switch that is off, a live
account, a strategy nothing has validated, a card that is no longer fresh, a signal it has already
taken. Each of those is a test that fails if the guard is removed, rather than a test that the
happy path works - the happy path is the least likely thing to be wrong.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from fakes import FakeMT5
from mt5_intel.config import Settings
from mt5_intel.execution import (
    CONFLUENCE,
    MAX_SELECTED_NAMES,
    MAX_SETUPS_AGE_S,
    MT5Trader,
    Switch,
    artifact,
    plans,
    read_selection,
    read_switch,
    run,
    size_position,
    strategies_to_trade,
)
from mt5_intel.store import Store

NOW = int(datetime(2026, 10, 13, 15, 0, tzinfo=UTC).timestamp())
OFFSET_S = 3 * 3600
VALIDATED = {"carry_trend", "sweep_reclaim"}


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "login": 1,
        "password": "not-a-real-password",
        "server": "HFMarketsSV-Demo",
        "terminal_path": None,
        "utc_offset_hours": 3.0,
        "symbol_root": "USDJPY",
        "reference_equity_zar": 100.0,
        "risk_pct": 1.0,
        "usd_zar_fallback": 18.0,
        "history_bars": 300_000,
        "spread_days": 30,
        "tick_watch_seconds": 30.0,
        "report_dir": Path("reports"),
        "auto_trade": True,
        "auto_allow_real": False,
        "auto_strategies": ("carry_trend",),
        "auto_max_positions": 2,
        "auto_magic": 20261013,
        "auto_risk_pct": 1.0,
    }
    base.update(overrides)
    return Settings(**base)


def _market(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "bar_age_s": 30,
        "spread_pips": 1.0,
        "market_open": True,
        "daily_loss_pct": 0.0,
        "open_positions": 0,
        "circuit_breaker_tripped": False,
        "news_verifiable": True,
        "news_excluded": False,
        "offset_s": OFFSET_S,
    }
    base.update(overrides)
    return base


def _card_row(
    family: str,
    *,
    side: str = "buy",
    epoch: int = NOW - OFFSET_S - 3600,
    entry: float = 150.100,
    sl: float = 149.800,
    tp: float = 150.700,
    bars_held: int = 0,
    stop_pips: float = 30.0,
    range_pos: float | None = None,
    fresh_high: bool | None = None,
    fresh_low: bool | None = None,
) -> dict[str, Any]:
    return {
        "family": family,
        "params": None,
        "status": "open",
        "side": side,
        "signal_epoch": epoch,
        "signal_utc": "2026-10-13 14:00",
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "stop_pips": stop_pips,
        "tp_r": 2.0,
        "bars_held": bars_held,
        "r_now": 0.0,
        # Where the market stood on this card's own signal bar. None is the honest default: a
        # payload written before the field existed must not fade on an invented placement.
        "range_pos": range_pos,
        "fresh_high": fresh_high,
        "fresh_low": fresh_low,
    }


def _payload(
    rows: list[dict[str, Any]],
    *,
    digits: int = 3,
    max_hold: int = 24,
    generated: str | None = None,
) -> dict[str, Any]:
    """A setups artifact. Stamped as written just now unless a test says otherwise."""
    stamp = datetime.fromtimestamp(NOW, UTC).isoformat() if generated is None else generated
    return {"generated_utc": stamp, "digits": digits, "max_hold_bars": max_hold, "setups": rows}


def _arm(tmp_path: Path, *, enabled: bool = True) -> Path:
    path = tmp_path / "auto-switch.json"
    path.write_text(
        json.dumps({"enabled": enabled, "updated_utc": "2026-10-13T12:00:00+00:00"}),
        encoding="utf-8",
    )
    return path


def _run(
    backend: FakeMT5,
    store: Store,
    settings: Settings,
    *,
    setups: dict[str, Any] | None,
    switch_path: Path,
    market: dict[str, Any] | None = None,
    validated: set[str] | None = None,
    selection_path: Path | None = None,
    registry: list[dict[str, Any]] | None = None,
    now_epoch: int = NOW,
) -> Any:
    return run(
        trader=MT5Trader(backend),
        store=store,
        settings=settings,
        setups=setups,
        market=_market() if market is None else market,
        switch_path=switch_path,
        selection_path=selection_path,
        now_epoch=now_epoch,
        offset_s=OFFSET_S,
        timeframe_s=3600,
        max_hold_bars=24,
        validated=VALIDATED if validated is None else validated,
        registry=registry,
    )


def _select(tmp_path: Path, strategies: list[str], *, allow_unvalidated: bool = False) -> Path:
    """A selection file, as the dashboard writes it. The acknowledgement is omitted when off."""
    path = tmp_path / "auto-strategies.json"
    payload: dict[str, Any] = {
        "strategies": strategies,
        "updated_utc": "2026-10-13T12:00:00+00:00",
    }
    if allow_unvalidated:
        payload["allow_unvalidated"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _backend(**overrides: Any) -> FakeMT5:
    return FakeMT5(now=datetime(2026, 10, 13, 15, 0, tzinfo=UTC), **overrides)


# --- the switch --------------------------------------------------------------------------


def test_the_master_switch_defaults_to_off_and_the_dashboard_cannot_arm_it(tmp_path: Path) -> None:
    switch = read_switch(_arm(tmp_path), master=False)
    assert switch.enabled is False
    assert switch.master is False
    assert "AUTO_TRADE" in switch.reason


def test_a_missing_switch_file_reads_as_off(tmp_path: Path) -> None:
    switch = read_switch(tmp_path / "auto-switch.json", master=True)
    assert switch.enabled is False
    assert switch.runtime is False


def test_a_malformed_switch_file_reads_as_off(tmp_path: Path) -> None:
    path = tmp_path / "auto-switch.json"
    path.write_text("{ not json", encoding="utf-8")
    assert read_switch(path, master=True).enabled is False


def test_an_armed_switch_needs_both_the_master_and_the_dashboard(tmp_path: Path) -> None:
    switch = read_switch(_arm(tmp_path), master=True)
    assert switch.enabled is True
    assert switch.master and switch.runtime


def test_switch_off_places_no_order_at_all(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(),
        setups=_payload([_card_row("carry_trend")]),
        switch_path=_arm(tmp_path, enabled=False),
    )
    assert backend.orders == []
    assert outcome.placed == []
    assert store.recent_auto_trades() == []
    assert outcome.switch is not None and outcome.switch.enabled is False


def test_master_off_is_not_armable_from_the_dashboard_file(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(auto_trade=False),
        setups=_payload([_card_row("carry_trend")]),
        switch_path=_arm(tmp_path, enabled=True),
    )
    assert backend.orders == []
    assert outcome.placed == []
    assert outcome.switch is not None and outcome.switch.runtime is True


# --- the account guard -------------------------------------------------------------------


def test_a_real_account_is_refused_without_the_second_flag(tmp_path: Path) -> None:
    backend = _backend(trade_mode=2)
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(auto_allow_real=False),
        setups=_payload([_card_row("carry_trend")]),
        switch_path=_arm(tmp_path),
    )
    assert backend.orders == []
    assert outcome.account["trade_mode"] == "REAL"
    assert any("REAL account" in item["reason"] for item in outcome.refused)


def test_a_demo_account_is_named_demo_and_not_unknown(tmp_path: Path) -> None:
    """DEMO is account trade_mode 0, and 0 is falsy - so this is the case an `or -1` reading misses."""
    backend = _backend(trade_mode=0)
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(),
        setups=_payload([_card_row("carry_trend")]),
        switch_path=_arm(tmp_path),
    )
    assert outcome.account["trade_mode"] == "DEMO"
    assert len(backend.orders) == 1  # and a demo is not held back by the live-account guard


def test_a_real_account_trades_when_the_second_flag_is_set(tmp_path: Path) -> None:
    backend = _backend(trade_mode=2)
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(auto_allow_real=True),
        setups=_payload([_card_row("carry_trend")]),
        switch_path=_arm(tmp_path),
    )
    assert len(backend.orders) == 1
    assert len(outcome.placed) == 1


# --- what may be traded ------------------------------------------------------------------


def test_a_listed_but_unvalidated_strategy_is_refused(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(auto_strategies=("rsi_mean_reversion",)),
        setups=_payload([_card_row("rsi_mean_reversion")]),
        switch_path=_arm(tmp_path),
    )
    assert backend.orders == []
    assert outcome.allow["allowed"] == []
    assert outcome.allow["refused"] == [
        {"strategy": "rsi_mean_reversion", "reason": "not historically validated on this timeframe"}
    ]


def test_a_validated_strategy_that_is_not_listed_is_not_traded(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(auto_strategies=()),
        setups=_payload([_card_row("carry_trend")]),
        switch_path=_arm(tmp_path),
    )
    assert backend.orders == []
    assert outcome.placed == []


def test_confluence_is_only_built_from_families_that_are_themselves_allowed() -> None:
    # sweep_reclaim is validated but not listed, so there is no agreement to trade as a pair and
    # carry_trend has to stand on its own.
    payload = _payload([_card_row("carry_trend"), _card_row("sweep_reclaim")])
    cards = plans(payload, ["carry_trend", CONFLUENCE], digits=3)
    assert [card.strategy for card in cards] == ["carry_trend"]


def test_confluence_with_nothing_to_combine_with_is_refused() -> None:
    allowed, refused = strategies_to_trade(_settings(auto_strategies=(CONFLUENCE,)), VALIDATED)
    assert allowed == []
    assert refused == [{"strategy": CONFLUENCE, "reason": "none of its families are validated"}]


def test_confluence_is_allowed_once_its_families_are() -> None:
    allowed, _ = strategies_to_trade(
        _settings(auto_strategies=("carry_trend", "sweep_reclaim", CONFLUENCE)), VALIDATED
    )
    assert allowed == ["carry_trend", "sweep_reclaim", CONFLUENCE]


def test_confluence_needs_two_allowed_families_to_pair() -> None:
    # One family cannot agree with itself, and the panel has to be able to say so rather than
    # showing the pair as armed while it can never fire.
    allowed, refused = strategies_to_trade(
        _settings(auto_strategies=("carry_trend", CONFLUENCE)), VALIDATED
    )
    assert allowed == ["carry_trend"]
    assert refused == [
        {"strategy": CONFLUENCE, "reason": "it needs two allowed families to pair; only carry_trend is allowed"}
    ]


def test_confluence_supersedes_the_halves_it_is_made_of() -> None:
    payload = _payload([_card_row("carry_trend"), _card_row("sweep_reclaim")])
    cards = plans(payload, ["carry_trend", "sweep_reclaim", CONFLUENCE], digits=3)
    assert len(cards) == 1
    assert cards[0].strategy == CONFLUENCE
    assert cards[0].families == ("carry_trend", "sweep_reclaim")


def test_confluence_takes_the_tightest_stop_of_the_agreeing_cards() -> None:
    tight = _card_row("sweep_reclaim", entry=150.100, sl=149.900, tp=150.500)
    wide = _card_row("carry_trend", entry=150.100, sl=149.500, tp=151.300)
    cards = plans(_payload([wide, tight]), ["carry_trend", "sweep_reclaim", CONFLUENCE], digits=3)
    assert [card.strategy for card in cards] == [CONFLUENCE]
    assert cards[0].sl == 149.900  # the tighter of the two stops, not the first row's


def test_a_held_trades_newest_signal_is_actable_without_waiting_for_the_exit() -> None:
    """A rule holding a trade acts on its newest re-fire the cycle it appears.

    The row's top-level card is the trade as taken (its epoch can never be newer than itself), so
    before the nested `latest_signal` was read the hand-off and reversal machinery could never
    fire: every fresh signal sat behind the live trade until TP or SL ended it. Now both cards
    surface, the newer one first, and a flip is visible as such.
    """
    held = _card_row("carry_trend", epoch=NOW - OFFSET_S - 7200, bars_held=8)
    flipped = dict(held)
    flipped["latest_signal"] = {
        "side": "sell",
        "signal_utc": "2026-10-13 16:00",
        "signal_epoch": NOW - OFFSET_S - 1800,
        "entry": 150.300,
        "sl": 150.600,
        "tp": 149.700,
        "stop_pips": 30.0,
        "tp_r": 2.0,
        "bars_held": 1,
        "r_now": 0.0,
    }
    cards = plans(_payload([flipped]), ["carry_trend"], digits=3)
    # The held bar is 8 old and past MAX_ENTRY_LAG_BARS (1): the only actable card is the
    # current signal - the flip, opposite side, its own levels.
    assert [card.strategy for card in cards] == ["carry_trend"]
    assert cards[0].side == "sell"
    assert cards[0].signal_epoch == NOW - OFFSET_S - 1800
    assert cards[0].entry == 150.300
    assert cards[0].sl == 150.600


def test_a_same_side_re_fired_signal_does_not_become_a_second_position() -> None:
    """The current signal of a same-side re-fire hands its target over, never doubles.

    Two same-side cards of one strategy surface, but the engine's twin rule still holds: the
    newer bar can only move the held trade's target, never open a second position on one symbol.
    """
    held = _card_row("carry_trend", epoch=NOW - OFFSET_S - 7200, bars_held=8)
    reinforced = dict(held)
    reinforced["latest_signal"] = {
        "side": "buy",
        "signal_utc": "2026-10-13 16:00",
        "signal_epoch": NOW - OFFSET_S - 1800,
        "entry": 150.100,
        "sl": 149.800,
        "tp": 151.100,  # a better target than the held trade's 150.700
        "stop_pips": 30.0,
        "tp_r": 3.0,
        "bars_held": 1,
        "r_now": 0.0,
    }
    cards = plans(_payload([reinforced]), ["carry_trend"], digits=3)
    assert len(cards) == 1
    assert cards[0].side == "buy"
    assert cards[0].signal_epoch == NOW - OFFSET_S - 1800
    assert cards[0].tp == 151.100  # the re-fire's own better target, ready to hand off


def test_agreeing_cards_on_opposite_sides_are_not_a_confluence() -> None:
    payload = _payload([_card_row("carry_trend"), _card_row("sweep_reclaim", side="sell", sl=150.400, tp=149.500)])
    assert plans(payload, [CONFLUENCE], digits=3) == []


def test_agreeing_cards_on_different_bars_are_not_a_confluence() -> None:
    payload = _payload(
        [
            _card_row("carry_trend"),
            _card_row("sweep_reclaim", epoch=NOW - OFFSET_S - 7200),
        ]
    )
    assert plans(payload, [CONFLUENCE], digits=3) == []


def test_the_fade_mode_sells_at_the_top_third_even_when_the_members_bought() -> None:
    """The account holder's placement rule flips the composite against the agreeing members.

    Both members agreed to buy near the top of the range; the fade rule says a high sells. The
    card carries the members' tightest card mirrored: the same entry, the same stop distance and
    the same 2R target ridden to the sell side, so sizing and R arithmetic are untouched - and the
    members themselves are superseded, exactly as the agreement they formed superseded them.
    """
    rows = [
        _card_row("carry_trend", range_pos=0.90, fresh_high=True),
        _card_row("sweep_reclaim", entry=150.100, sl=149.900, tp=150.500, range_pos=0.90),
    ]
    cards = plans(
        _payload(rows),
        ["carry_trend", "sweep_reclaim", CONFLUENCE],
        digits=3,
        confluence_mode="fade",
    )
    assert [card.strategy for card in cards] == [CONFLUENCE]
    assert cards[0].side == "sell"
    assert cards[0].families == ("carry_trend", "sweep_reclaim")
    assert cards[0].entry == 150.100
    assert cards[0].sl == pytest.approx(150.300)  # the mirrored stop: the same 0.2R distance above
    assert cards[0].tp == pytest.approx(149.700)  # the mirrored target: the same 2R below


def test_the_fade_mode_buys_at_the_bottom_third_and_agrees_with_buying_members() -> None:
    rows = [
        _card_row("carry_trend", range_pos=0.15),
        _card_row("sweep_reclaim", range_pos=0.15),
    ]
    cards = plans(
        _payload(rows),
        ["carry_trend", "sweep_reclaim", CONFLUENCE],
        digits=3,
        confluence_mode="fade",
    )
    assert [card.strategy for card in cards] == [CONFLUENCE]
    assert cards[0].side == "buy"
    assert cards[0].sl == 149.800  # nothing to mirror: the fade and the members said one thing


def test_the_fade_mode_stands_down_in_the_middle_of_the_range() -> None:
    """The middle third decides nothing, so the composite trades nothing - and the members keep
    trading their own cards individually, which is what the panel says when it reports it."""
    rows = [
        _card_row("carry_trend", range_pos=0.50),
        _card_row("sweep_reclaim", range_pos=0.50),
    ]
    cards = plans(
        _payload(rows),
        ["carry_trend", "sweep_reclaim", CONFLUENCE],
        digits=3,
        confluence_mode="fade",
    )
    assert [card.strategy for card in cards] == ["carry_trend", "sweep_reclaim"]


def test_an_unreadable_placement_stands_down_instead_of_fading_on_a_guess() -> None:
    rows = [_card_row("carry_trend"), _card_row("sweep_reclaim")]
    cards = plans(
        _payload(rows),
        ["carry_trend", "sweep_reclaim", CONFLUENCE],
        digits=3,
        confluence_mode="fade",
    )
    assert [card.strategy for card in cards] == ["carry_trend", "sweep_reclaim"]


def test_the_fade_mode_flipping_a_sell_group_of_the_same_bar_folds_both_groups_into_one() -> None:
    """Two agreeing groups on one bar fade to the same side and become one composite.

    A buy group and a sell group on the same bar both fade to a sell at the top third; two orders
    on one bar would double it, so they fold - the tighter geometry wins and the name carries
    every family that asked for it, exactly as one bar's re-fires are folded above.
    """
    rows = [
        _card_row("carry_trend", range_pos=0.85),
        _card_row("sweep_reclaim", range_pos=0.85),
        _card_row("trend_pullback", side="sell", sl=150.400, tp=149.500, range_pos=0.85),
        _card_row("ma_crossover", side="sell", sl=150.350, tp=149.300, range_pos=0.85),
    ]
    cards = plans(
        _payload(rows),
        ["carry_trend", "sweep_reclaim", "trend_pullback", "ma_crossover", CONFLUENCE],
        digits=3,
        confluence_mode="fade",
    )
    assert [card.strategy for card in cards] == [CONFLUENCE]
    assert cards[0].side == "sell"
    assert cards[0].families == (
        "carry_trend",
        "ma_crossover",
        "sweep_reclaim",
        "trend_pullback",
    )
    assert cards[0].sl == 150.350  # the tightest stop across both faded groups, as the order carries it


def test_the_members_mode_ignores_the_placement_particularly_the_new_fields() -> None:
    rows = [
        _card_row("carry_trend", range_pos=0.90, fresh_high=True),
        _card_row("sweep_reclaim", entry=150.100, sl=149.900, tp=150.500, range_pos=0.90),
    ]
    cards = plans(_payload(rows), ["carry_trend", "sweep_reclaim", CONFLUENCE], digits=3)
    assert [card.strategy for card in cards] == [CONFLUENCE]
    assert cards[0].side == "buy"  # the default mode follows the members, wherever the market sat


def _select_confluence(tmp_path: Path) -> Path:
    return _select(tmp_path, ["carry_trend", "sweep_reclaim", "confluence"])


def _agreeing_payload() -> dict[str, Any]:
    return _payload([_card_row("carry_trend"), _card_row("sweep_reclaim")])


def _charged_positions(backend: FakeMT5) -> None:
    """The trades the stop is judged against: a hand-opened opposing sell, and two it must keep.

    * a sell on the confluence's own symbol opened by hand (no magic of this system's) - closed.
    * a buy on the same symbol - the composite's ally, left running.
    * a sell on another symbol - another symbol's cycle decides about it, not this one.
    """
    backend.add_position(7002, kind=1, symbol="USDJPYc")
    backend.extra_positions[0].magic = 0
    backend.add_position(7003, kind=0, symbol="USDJPYc")
    backend.add_position(7004, kind=1, symbol="EURUSDc")


def test_a_confluence_signal_closes_every_opposing_position_on_its_symbol(tmp_path: Path) -> None:
    """The stop closes the opposing side on this symbol, manual trades included - and only there.

    `by all` names positions this system never opened, and the symbol is the boundary: the same
    run that closes a hand-opened sell on USDJPY leaves the same-side buy alone and does not
    touch an opposing sell on EURUSDc, which belongs to its own watch cycle.
    """
    backend = _backend()
    _charged_positions(backend)
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(auto_strategies=("carry_trend", "sweep_reclaim", CONFLUENCE)),
        setups=_agreeing_payload(),
        switch_path=_arm(tmp_path),
        selection_path=_select_confluence(tmp_path),
    )
    assert any(row.get("position") == 7002 for row in backend.orders)  # the hand-opened opposing sell is stopped
    assert [row["reason"] for row in outcome.closed if row["strategy"] == "confluence stop"] == [
        "opposing sell to a confluence buy on USDJPYc"
    ]
    assert any(note.startswith("Confluence stop: closed 1 opposing sell") for note in outcome.notes)
    # The ally and the foreign symbol's position are never sent anything.
    assert not any(
        row.get("position") in (7003, 7004) for row in backend.orders
    )


def test_the_confluence_stop_fires_once_per_signal(tmp_path: Path) -> None:
    """A second cycle on the same signal closes nothing new, so a hand re-open is safe.

    The occurrence is journaled on (symbol, signal bar, side) and re-read before firing; while
    the same bar is still within its entry window nothing fires again. The composite itself may
    still place its own entry in a later cycle - the stop and the entry are separate decisions.
    """
    backend = _backend()
    _charged_positions(backend)
    store = Store(tmp_path / "m.db")
    setups = _agreeing_payload()
    settings = _settings(auto_strategies=("carry_trend", "sweep_reclaim", CONFLUENCE))
    switch_path = _arm(tmp_path)
    selection_path = _select_confluence(tmp_path)
    _run(backend, store, settings, setups=setups, switch_path=switch_path, selection_path=selection_path)
    assert len([row for row in backend.orders if row.get("position") == 7002]) == 1
    assert len(store.recent_events("confluence_stop", 10)) == 1

    second = _run(backend, store, settings, setups=setups, switch_path=switch_path, selection_path=selection_path)
    assert len([row for row in backend.orders if row.get("position") == 7002]) == 1  # not fired again
    assert [row for row in second.closed if row["strategy"] == "confluence stop"] == []


def test_the_confluence_stop_applies_only_when_confluence_is_on_the_allow_list(tmp_path: Path) -> None:
    backend = _backend()
    backend.add_position(7002, kind=1, symbol="USDJPYc")
    backend.extra_positions[0].magic = 0
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(auto_strategies=("carry_trend", "sweep_reclaim")),
        setups=_agreeing_payload(),
        switch_path=_arm(tmp_path),
    )
    assert [row for row in backend.orders if row.get("position") == 7002] == []
    assert all(row["strategy"] != "confluence stop" for row in outcome.closed)


def test_a_disarmed_switch_closes_no_opposing_positions(tmp_path: Path) -> None:
    backend = _backend()
    backend.add_position(7002, kind=1, symbol="USDJPYc")
    backend.extra_positions[0].magic = 0
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(auto_strategies=("carry_trend", "sweep_reclaim", CONFLUENCE)),
        setups=_agreeing_payload(),
        switch_path=_arm(tmp_path, enabled=False),
        selection_path=_select_confluence(tmp_path),
    )
    assert [row for row in backend.orders if row.get("position") == 7002] == []
    assert all(row["strategy"] != "confluence stop" for row in outcome.closed)


# --- freshness ---------------------------------------------------------------------------


def test_a_card_that_stopped_being_fresh_is_not_entered(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(),
        setups=_payload([_card_row("carry_trend", bars_held=3)]),
        switch_path=_arm(tmp_path),
    )
    # The rule is in a trade, but this is not the moment it was entered: joining it hours later is
    # exactly the late entry that makes a working strategy look broken.
    assert backend.orders == []
    assert outcome.placed == []


def test_a_price_that_has_already_run_past_the_entry_is_not_chased(tmp_path: Path) -> None:
    backend = _backend(advance_ticks=False)  # the tick sits at 150.114 ask
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(),
        # Planned entry well below the market: the move this card pointed at has already happened.
        setups=_payload([_card_row("carry_trend", entry=149.000, sl=148.700, tp=149.600)]),
        switch_path=_arm(tmp_path),
    )
    assert backend.orders == []
    assert any("already moved" in item["reason"] for item in outcome.refused)


def test_a_stale_setups_view_blocks_new_entries(tmp_path: Path) -> None:
    """A view that stopped being rebuilt can still present a card as "just entered".

    The price guard catches most of that, but not a market that has come back to the planned entry:
    the card would then be taken at a bar it never pointed at. So the artifact's own age is checked,
    and trading stops before the price check is even reached.
    """
    backend = _backend(advance_ticks=False)
    store = Store(tmp_path / "m.db")
    stale = datetime.fromtimestamp(NOW - MAX_SETUPS_AGE_S - 60, UTC).isoformat()
    outcome = _run(
        backend,
        store,
        _settings(),
        setups=_payload([_card_row("carry_trend")], generated=stale),
        switch_path=_arm(tmp_path),
    )
    assert backend.orders == []
    assert any("setups view" in note for note in outcome.notes)


def test_a_setups_view_with_no_age_cannot_be_traded_from(tmp_path: Path) -> None:
    """An unreadable age is treated as too old, like an unverifiable spread in the gate."""
    backend = _backend(advance_ticks=False)
    store = Store(tmp_path / "m.db")
    payload = _payload([_card_row("carry_trend", entry=150.100)])
    del payload["generated_utc"]
    outcome = _run(backend, store, _settings(), setups=payload, switch_path=_arm(tmp_path))
    assert backend.orders == []
    assert any("not stamped" in note for note in outcome.notes)


def test_a_fresh_setups_view_is_traded_from(tmp_path: Path) -> None:
    """The counterpart, so the age guard cannot be satisfied by refusing everything."""
    backend = _backend(advance_ticks=False)
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(),
        setups=_payload([_card_row("carry_trend", entry=150.100)]),
        switch_path=_arm(tmp_path),
    )
    assert len(backend.orders) == 1
    assert outcome.allow["setups_age_s"] == 0


def test_a_card_at_its_entry_is_taken(tmp_path: Path) -> None:
    backend = _backend(advance_ticks=False)  # bid 150.100, ask 150.114
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(),
        setups=_payload([_card_row("carry_trend", entry=150.100, sl=149.800, tp=150.700)]),
        switch_path=_arm(tmp_path),
    )
    assert len(backend.orders) == 1
    assert len(outcome.placed) == 1


# --- the order itself --------------------------------------------------------------------


def test_the_order_carries_the_cards_own_stop_and_target(tmp_path: Path) -> None:
    backend = _backend(advance_ticks=False)
    store = Store(tmp_path / "m.db")
    _run(
        backend,
        store,
        _settings(auto_magic=777),
        setups=_payload([_card_row("carry_trend", entry=150.100, sl=149.800, tp=150.700)]),
        switch_path=_arm(tmp_path),
    )
    request = backend.orders[0]
    assert request["action"] == backend.TRADE_ACTION_DEAL
    assert request["type"] == backend.ORDER_TYPE_BUY
    assert request["symbol"] == "USDJPY"
    assert request["sl"] == 149.800
    assert request["tp"] == 150.700
    assert request["magic"] == 777
    assert request["price"] == 150.114  # a buy fills on the ask
    assert "MTI carry_trend" == request["comment"]


def test_a_sell_fills_on_the_bid_and_uses_the_other_side(tmp_path: Path) -> None:
    backend = _backend(advance_ticks=False)
    store = Store(tmp_path / "m.db")
    _run(
        backend,
        store,
        _settings(),
        setups=_payload([_card_row("carry_trend", side="sell", entry=150.100, sl=150.400, tp=149.500)]),
        switch_path=_arm(tmp_path),
    )
    request = backend.orders[0]
    assert request["type"] == backend.ORDER_TYPE_SELL
    assert request["price"] == 150.100  # a sell fills on the bid


def test_the_signal_is_recorded_with_its_ticket_and_result(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    _run(
        backend,
        store,
        _settings(),
        setups=_payload([_card_row("carry_trend")]),
        switch_path=_arm(tmp_path),
    )
    rows = store.recent_auto_trades()
    assert len(rows) == 1
    assert rows[0]["status"] == "placed"
    assert rows[0]["ticket"] is not None
    assert rows[0]["strategy"] == "carry_trend"
    assert rows[0]["expires_epoch"] is not None


def test_a_broker_refusal_is_journaled_and_does_not_raise(tmp_path: Path) -> None:
    backend = _backend(order_retcode=10016, order_comment="Invalid stops")
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(),
        setups=_payload([_card_row("carry_trend")]),
        switch_path=_arm(tmp_path),
    )
    assert outcome.placed == []
    assert any("Invalid stops" in item["reason"] for item in outcome.refused)
    rows = store.recent_auto_trades()
    assert rows[0]["status"] == "rejected"
    assert rows[0]["retcode"] == 10016


def test_a_filling_mode_the_symbol_refuses_is_retried_with_the_next_one(tmp_path: Path) -> None:
    """Retcode 10030 refused every order this project sent, and the mode was the reason.

    The symbol advertises FOK only, so IOC is not offered and FOK is asked for first; FOK is
    refused here so RETURN is tried. Two requests, in that order: the second is the same intent
    again, because a 10030 executed nothing.
    """
    backend = _backend(order_retcode=10030, order_comment="Unsupported filling mode")
    original = backend.symbol_info

    def fok_only(name: str) -> Any:
        spec = original(name)
        spec.filling_mode = 1  # SYMBOL_FILLING_FOK
        return spec

    backend.symbol_info = fok_only  # type: ignore[method-assign]
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(),
        setups=_payload([_card_row("carry_trend")]),
        switch_path=_arm(tmp_path),
    )
    assert [request["type_filling"] for request in backend.orders] == [
        FakeMT5.ORDER_FILLING_FOK,
        FakeMT5.ORDER_FILLING_RETURN,
    ]
    # A refusal for any other reason stops the walk: the symbol is asked once, not three times.
    assert outcome.placed == []
    assert store.recent_auto_trades()[0]["status"] == "rejected"


def test_a_filling_refusal_that_never_clears_is_still_only_two_requests(tmp_path: Path) -> None:
    """The walk is bounded by the symbol's own answer, not by the number of modes that exist."""
    backend = _backend(order_retcode=10030, order_comment="Unsupported filling mode")
    store = Store(tmp_path / "m.db")
    _run(
        backend,
        store,
        _settings(),
        setups=_payload([_card_row("carry_trend")]),
        switch_path=_arm(tmp_path),
    )
    # The fake reports no filling mask, so IOC alone is kept - the behaviour that was there before,
    # and not a burst of retries against a symbol whose preferences are unknown.
    assert len(backend.orders) == 1
    assert backend.orders[0]["type_filling"] == FakeMT5.ORDER_FILLING_IOC


def test_a_broker_refusal_is_still_on_the_artifact_cycles_later(tmp_path: Path) -> None:
    """A refused signal is spent, so the cycle that saw it cannot be the only one that reports it.

    This is the difference between a panel that says "the broker refused this" and one that shows an
    armed switch, no positions and nothing to explain why - which is what was on screen while every
    entry was being refused for its filling mode.
    """
    backend = _backend(order_retcode=10016, order_comment="Invalid stops")
    store = Store(tmp_path / "m.db")
    setups = _payload([_card_row("carry_trend")])
    switch = _arm(tmp_path)
    _run(backend, store, _settings(), setups=setups, switch_path=switch)
    later = _run(backend, store, _settings(), setups=setups, switch_path=switch)

    assert later.placed == []
    assert later.refused == []  # this cycle attempted nothing: the signal is already spent
    assert [row["strategy"] for row in later.rejected] == ["carry_trend"]
    assert later.rejected[0]["retcode"] == 10016
    view = artifact(later, _settings(), market=_market(), now_epoch=NOW)
    assert view["rejected"][0]["broker_comment"] == "Invalid stops"


def test_a_terminal_that_raises_is_a_refusal_not_a_crash(tmp_path: Path) -> None:
    backend = _backend(order_raises=True)
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(),
        setups=_payload([_card_row("carry_trend")]),
        switch_path=_arm(tmp_path),
    )
    assert outcome.placed == []
    assert any("RuntimeError" in item["reason"] for item in outcome.refused)


def test_a_rejected_signal_is_not_retried_on_the_next_cycle(tmp_path: Path) -> None:
    backend = _backend(order_retcode=10016)
    store = Store(tmp_path / "m.db")
    setups = _payload([_card_row("carry_trend")])
    _run(backend, store, _settings(), setups=setups, switch_path=_arm(tmp_path))
    _run(backend, store, _settings(), setups=setups, switch_path=_arm(tmp_path))
    # One attempt, not one per cycle for as long as the card is fresh.
    assert len(backend.orders) == 1


def test_the_same_signal_is_never_opened_twice(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    setups = _payload([_card_row("carry_trend")])
    switch = _arm(tmp_path)
    first = _run(backend, store, _settings(), setups=setups, switch_path=switch)
    second = _run(backend, store, _settings(), setups=setups, switch_path=switch)
    assert len(first.placed) == 1
    assert len(backend.orders) == 1
    assert second.placed == []


def test_a_second_side_on_the_same_bar_is_its_own_signal(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    row = _card_row("carry_trend", side="sell", sl=150.400, tp=149.500)
    _run(backend, store, _settings(), setups=_payload([row, _card_row("carry_trend")]), switch_path=_arm(tmp_path))
    # Both sides are distinct claims in the journal, so the second is not blocked by the first.
    assert store.auto_trade_taken("carry_trend", row["signal_epoch"], "sell", "USDJPY") is True
    assert store.auto_trade_taken("carry_trend", row["signal_epoch"], "buy", "USDJPY") is True


# --- the gate ----------------------------------------------------------------------------


def test_a_wide_spread_blocks_the_entry_with_the_reason_named(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(),
        setups=_payload([_card_row("carry_trend")]),
        switch_path=_arm(tmp_path),
        market=_market(spread_pips=99.0),
    )
    assert backend.orders == []
    assert any("SPREAD_TOO_WIDE" in item["reason"] for item in outcome.refused)


def test_a_tripped_circuit_breaker_blocks_the_entry(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(),
        setups=_payload([_card_row("carry_trend")]),
        switch_path=_arm(tmp_path),
        market=_market(circuit_breaker_tripped=True),
    )
    assert backend.orders == []
    assert any("FAST_MOVE_CIRCUIT_BREAKER" in item["reason"] for item in outcome.refused)


def test_an_unreadable_spread_blocks_the_entry(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(),
        setups=_payload([_card_row("carry_trend")]),
        switch_path=_arm(tmp_path),
        market=_market(spread_pips=None),
    )
    assert backend.orders == []
    assert any("SPREAD_UNVERIFIED" in item["reason"] for item in outcome.refused)


# --- the position cap --------------------------------------------------------------------


def test_the_auto_position_cap_is_respected(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    rows = [
        _card_row("carry_trend"),
        _card_row("sweep_reclaim"),
    ]
    outcome = _run(
        backend,
        store,
        _settings(auto_max_positions=1, auto_strategies=("carry_trend", "sweep_reclaim")),
        setups=_payload(rows),
        switch_path=_arm(tmp_path),
    )
    assert len(backend.orders) == 1
    assert len(outcome.placed) == 1
    assert any("cap" in item["reason"] for item in outcome.refused)


def test_a_same_strategy_lockup_with_an_open_trade_but_no_cap_blocked_by_cap(tmp_path: Path) -> None:
    """A live trade and a same-strategy card arriving for one more entry: the card is a twin, and
    the twin rule answers before the cap can. The journal names the position for the panel."""
    backend = _backend()
    backend.add_position(9001, volume=0.10)
    store = Store(tmp_path / "m.db")
    _seed_open(store, backend, expires_epoch=NOW + OFFSET_S + 86_400)
    outcome = _run(
        backend,
        store,
        _settings(auto_max_positions=2),
        setups=_payload([_card_row("carry_trend", epoch=NOW - OFFSET_S)]),
        switch_path=_arm(tmp_path),
    )
    assert backend.orders == []
    assert any("live" in item["reason"] for item in outcome.refused)


# --- exits -------------------------------------------------------------------------------


def _seed_open(store: Store, backend: FakeMT5, *, ticket: int = 9001, symbol: str = "USDJPY", **overrides: Any) -> int:
    """An auto trade the journal believes is open, with a live position behind it."""
    values: dict[str, Any] = {
        "strategy": "carry_trend",
        "families": ("carry_trend",),
        "side": "buy",
        "signal_epoch": NOW - OFFSET_S - 7200,
        "signal_utc": "2026-10-13 12:00",
        "entry": 150.100,
        "sl": 149.800,
        "tp": 150.700,
        "volume": 0.10,
        "stop_pips": 30.0,
        "tp_r": 2.0,
        "risk_amount": 10.0,
        "equity": 1000.0,
        "risk_pct": 1.0,
        "digits": 3,
        "expires_epoch": NOW + OFFSET_S + 86_400,
        "magic": 20261013,
        "code_version": "auto-v1",
        "open_ts": "2026-10-13T12:00:00+00:00",
    }
    values.update(overrides)
    row_id = store.reserve_auto_trade(symbol=symbol, **values)
    assert row_id is not None
    store.settle_auto_trade(row_id, status="placed", ticket=ticket, retcode=10009)
    return row_id


def test_a_position_the_broker_closed_is_reconciled_and_frees_its_slot(tmp_path: Path) -> None:
    backend = _backend()  # holds no positions: the stop or target already took it
    store = Store(tmp_path / "m.db")
    row_id = _seed_open(store, backend)
    outcome = _run(
        backend,
        store,
        _settings(),
        setups=_payload([]),
        switch_path=_arm(tmp_path),
    )
    assert backend.orders == []
    assert store.auto_trades_open() == []
    assert {"strategy": "carry_trend", "ticket": 9001, "reason": "broker"} in outcome.closed
    assert any(row["id"] == row_id for row in store.recent_auto_trades() if row["status"] == "closed")


def test_the_cards_time_limit_closes_the_position(tmp_path: Path) -> None:
    backend = _backend()
    backend.add_position(9001, volume=0.10)
    store = Store(tmp_path / "m.db")
    # The hold limit ran out two bars ago.
    _seed_open(store, backend, expires_epoch=NOW + OFFSET_S - 7200)
    outcome = _run(
        backend,
        store,
        _settings(),
        setups=_payload([]),
        switch_path=_arm(tmp_path),
    )
    assert len(backend.orders) == 1
    request = backend.orders[0]
    assert request["position"] == 9001
    assert request["type"] == backend.ORDER_TYPE_SELL  # closing a long sells it
    assert store.auto_trades_open() == []
    assert outcome.closed[0]["reason"] == "time limit"


def test_a_position_still_inside_its_window_is_left_alone(tmp_path: Path) -> None:
    backend = _backend()
    backend.add_position(9001, volume=0.10)
    store = Store(tmp_path / "m.db")
    _seed_open(store, backend, expires_epoch=NOW + OFFSET_S + 86_400)
    outcome = _run(
        backend,
        store,
        _settings(),
        setups=_payload([]),
        switch_path=_arm(tmp_path),
    )
    assert backend.orders == []
    assert len(store.auto_trades_open()) == 1
    assert outcome.closed == []


def test_an_open_trade_still_counts_against_the_cap(tmp_path: Path) -> None:
    backend = _backend()
    backend.add_position(9001, volume=0.10)
    store = Store(tmp_path / "m.db")
    # A different strategy than the open trade's, so the twin rule for the hand-off does not
    # answer before the cap can: this tests the cap itself.
    _seed_open(store, backend, expires_epoch=NOW + OFFSET_S + 86_400, strategy="sweep_reclaim", families=("sweep_reclaim",))
    outcome = _run(
        backend,
        store,
        _settings(auto_max_positions=1, auto_strategies=("carry_trend", "sweep_reclaim")),
        setups=_payload([_card_row("carry_trend", epoch=NOW - OFFSET_S)]),
        switch_path=_arm(tmp_path),
        validated={"carry_trend", "sweep_reclaim"},
    )
    assert backend.orders == []
    assert any("cap" in item["reason"] for item in outcome.refused)


# --- sizing ------------------------------------------------------------------------------


def test_size_never_exceeds_the_risk_budget() -> None:
    sizing = size_position(
        equity=1000.0,
        risk_pct=1.0,
        stop_distance=0.300,
        tick_size=0.001,
        tick_value=0.6667,
        volume_min=0.01,
        volume_step=0.01,
        volume_max=100.0,
    )
    assert sizing.fits is True
    # 300 ticks x 0.6667 = 200.01 per lot, so 10.00 of risk buys 4.99 lots of a hundredth - floored
    # to 0.04, never up: 0.05 would risk 10.0005, which is over the budget.
    assert sizing.volume == 0.04
    assert sizing.volume * sizing.risk_per_lot <= sizing.risk_amount + 1e-9


def test_a_minimum_lot_that_risks_too_much_does_not_fit() -> None:
    sizing = size_position(
        equity=10.0,
        risk_pct=1.0,
        stop_distance=0.300,
        tick_size=0.001,
        tick_value=0.6667,
        volume_min=0.01,
        volume_step=0.01,
        volume_max=100.0,
    )
    assert sizing.fits is False
    assert "risks more than" in (sizing.reason or "")


def test_no_risk_budget_does_not_fit() -> None:
    # A Settings built by hand defaults to no risk budget, which must size to nothing rather than
    # to something: the safe reading of an unset risk is "do not trade".
    sizing = size_position(
        equity=1000.0,
        risk_pct=0.0,
        stop_distance=0.300,
        tick_size=0.001,
        tick_value=0.6667,
        volume_min=0.01,
        volume_step=0.01,
        volume_max=100.0,
    )
    assert sizing.fits is False
    assert "risk budget" in (sizing.reason or "")


def test_unreadable_sizing_inputs_do_not_fit() -> None:
    assert size_position(
        equity=1000.0,
        risk_pct=1.0,
        stop_distance=0.0,
        tick_size=0.001,
        tick_value=0.6667,
        volume_min=0.01,
        volume_step=0.01,
        volume_max=100.0,
    ).fits is False
    assert size_position(
        equity=1000.0,
        risk_pct=1.0,
        stop_distance=0.3,
        tick_size=0.001,
        tick_value=0.0,
        volume_min=0.01,
        volume_step=0.01,
        volume_max=100.0,
    ).fits is False


def test_an_equity_that_cannot_be_read_places_nothing(tmp_path: Path) -> None:
    backend = _backend(equity=0.0)
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(),
        setups=_payload([_card_row("carry_trend")]),
        switch_path=_arm(tmp_path),
    )
    assert backend.orders == []
    assert any("equity" in item["reason"] for item in outcome.refused)


def test_a_symbol_the_broker_will_not_trade_places_nothing(tmp_path: Path) -> None:
    backend = _backend()
    original = backend.symbol_info

    def close_only(name: str) -> Any:
        spec = original(name)
        spec.trade_mode = 3  # CLOSE_ONLY
        return spec

    backend.symbol_info = close_only  # type: ignore[method-assign]
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(),
        setups=_payload([_card_row("carry_trend")]),
        switch_path=_arm(tmp_path),
    )
    assert backend.orders == []
    assert any("does not allow trading" in item["reason"] for item in outcome.refused)


# --- the artifact the dashboard reads ----------------------------------------------------


def test_the_artifact_says_why_it_is_idle_when_the_switch_is_off(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(),
        setups=_payload([_card_row("carry_trend")]),
        switch_path=_arm(tmp_path, enabled=False),
    )
    payload = artifact(outcome, _settings(), market=_market(), now_epoch=NOW)
    assert payload["switch"]["enabled"] is False
    assert payload["notes"]
    assert payload["open_count"] == 0


def test_the_artifact_carries_the_placed_trade_and_the_allow_list(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(),
        setups=_payload([_card_row("carry_trend")]),
        switch_path=_arm(tmp_path),
    )
    payload = artifact(outcome, _settings(), market=_market(), now_epoch=NOW)
    assert payload["switch"]["enabled"] is True
    assert payload["allow"]["allowed"] == ["carry_trend"]
    assert payload["placed"][0]["strategy"] == "carry_trend"
    assert payload["open_count"] == 1


def test_a_switch_round_trips_through_json() -> None:
    switch = Switch(enabled=True, master=True, runtime=True, reason="Armed")
    assert json.loads(json.dumps(switch.as_dict()))["enabled"] is True


# --- which strategies, and who chose them -------------------------------------------------


def test_a_missing_selection_file_falls_back_to_the_env(tmp_path: Path) -> None:
    selection = read_selection(tmp_path / "nothing.json", _settings(auto_strategies=("carry_trend",)))
    assert selection.strategies == ("carry_trend",)
    assert selection.source == "env"
    assert selection.allow_unvalidated is False


def test_a_malformed_selection_file_falls_back_to_the_env(tmp_path: Path) -> None:
    path = tmp_path / "auto-strategies.json"
    path.write_text("{ not json", encoding="utf-8")
    selection = read_selection(path, _settings(auto_strategies=("carry_trend",)))
    assert selection.strategies == ("carry_trend",)
    assert selection.source == "env"


def test_a_selection_file_without_the_acknowledgement_reads_as_off(tmp_path: Path) -> None:
    path = tmp_path / "auto-strategies.json"
    path.write_text(json.dumps({"strategies": ["rsi_mean_reversion"]}), encoding="utf-8")
    selection = read_selection(path, _settings())
    assert selection.strategies == ("rsi_mean_reversion",)
    assert selection.allow_unvalidated is False
    # A value that is not `true` is not an acknowledgement, whatever it says.
    path.write_text(
        json.dumps({"strategies": ["rsi_mean_reversion"], "allow_unvalidated": "yes"}),
        encoding="utf-8",
    )
    assert read_selection(path, _settings()).allow_unvalidated is False


def test_the_selection_file_carries_the_confluence_mode_and_only_the_two_words(tmp_path: Path) -> None:
    path = tmp_path / "auto-strategies.json"
    path.write_text(
        json.dumps({"strategies": ["carry_trend"], "confluence_mode": "fade"}),
        encoding="utf-8",
    )
    assert read_selection(path, _settings()).confluence_mode == "fade"
    # Anything else - a missing field, a typo, a third word - reads as the default, which is the
    # behaviour the research numbers were recorded under.
    path.write_text(
        json.dumps({"strategies": ["carry_trend"], "confluence_mode": "FADE please"}),
        encoding="utf-8",
    )
    selection = read_selection(path, _settings())
    assert selection.confluence_mode == "members"
    assert selection.source == "dashboard"


def test_the_env_fallback_names_the_members_mode(tmp_path: Path) -> None:
    selection = read_selection(tmp_path / "nothing.json", _settings(auto_strategies=("carry_trend",)))
    assert selection.confluence_mode == "members"


def test_a_selection_file_cannot_grow_an_unbounded_allow_list(tmp_path: Path) -> None:
    path = tmp_path / "auto-strategies.json"
    path.write_text(
        json.dumps(
            {
                "strategies": [f"family_{index}" for index in range(200)]
                + ["x" * 200]
                + ["carry_trend"],
            }
        ),
        encoding="utf-8",
    )
    selection = read_selection(path, _settings())
    assert len(selection.strategies) == MAX_SELECTED_NAMES
    assert all(len(name) <= 40 for name in selection.strategies)


def test_the_dashboard_selection_overrides_the_env_list(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(auto_strategies=("carry_trend",)),
        setups=_payload([_card_row("carry_trend"), _card_row("sweep_reclaim")]),
        switch_path=_arm(tmp_path),
        selection_path=_select(tmp_path, ["sweep_reclaim"]),
    )
    assert outcome.allow["requested"] == ["sweep_reclaim"]
    assert outcome.allow["allowed"] == ["sweep_reclaim"]
    assert outcome.allow["selection"]["source"] == "dashboard"
    assert [order["comment"] for order in backend.orders] == ["MTI sweep_reclaim"]


def test_an_unvalidated_strategy_is_refused_even_when_it_is_chosen(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(auto_strategies=()),
        setups=_payload([_card_row("rsi_mean_reversion")]),
        switch_path=_arm(tmp_path),
        selection_path=_select(tmp_path, ["rsi_mean_reversion"]),
    )
    assert backend.orders == []
    assert outcome.allow["unvalidated"] == []
    assert outcome.allow["refused"] == [
        {"strategy": "rsi_mean_reversion", "reason": "not historically validated on this timeframe"}
    ]


def test_the_acknowledgement_trades_an_unvalidated_strategy_and_says_so(tmp_path: Path) -> None:
    """The one way past the validated-strategy rule, and it leaves a trace everywhere."""
    backend = _backend()
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(auto_strategies=()),
        setups=_payload([_card_row("rsi_mean_reversion")]),
        switch_path=_arm(tmp_path),
        selection_path=_select(tmp_path, ["rsi_mean_reversion"], allow_unvalidated=True),
    )
    assert len(backend.orders) == 1
    assert outcome.allow["unvalidated"] == ["rsi_mean_reversion"]
    assert any("without a validated edge" in note for note in outcome.notes)
    # The trade is recorded as what it is, so nothing downstream can mistake it for a validated one.
    assert outcome.placed[0]["validated"] is False
    placed = [event for event in store.recent_events("auto_placed")]
    assert placed and placed[0]["payload"]["validated"] is False


def test_the_env_acknowledgement_works_the_same_without_a_selection_file(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(auto_strategies=("rsi_mean_reversion",), auto_allow_unvalidated=True),
        setups=_payload([_card_row("rsi_mean_reversion")]),
        switch_path=_arm(tmp_path),
    )
    assert len(backend.orders) == 1
    assert outcome.allow["selection"]["source"] == "env"
    assert outcome.placed[0]["validated"] is False


def test_a_validated_card_is_still_flagged_validated(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    outcome = _run(
        backend,
        store,
        _settings(),
        setups=_payload([_card_row("carry_trend")]),
        switch_path=_arm(tmp_path),
    )
    assert outcome.placed[0]["validated"] is True


# --- the terminal itself ------------------------------------------------------------------


def test_a_terminal_with_algo_trading_off_places_nothing_and_names_the_button(tmp_path: Path) -> None:
    box = _backend(terminal_trade_allowed=False)
    store = Store(tmp_path / "m.db")
    outcome = _run(
        box,
        store,
        _settings(),
        setups=_payload([_card_row("carry_trend")]),
        switch_path=_arm(tmp_path),
    )
    assert box.orders == []
    assert outcome.placed == []
    assert outcome.refused[0]["strategy"] == "*"
    assert "Algo Trading" in outcome.refused[0]["reason"]
    assert "10027" in outcome.refused[0]["reason"]
    assert outcome.open_rows == []


def test_a_terminal_that_will_not_say_whether_trading_is_allowed_places_nothing(
    tmp_path: Path,
) -> None:
    box = _backend(terminal_trade_allowed=None)
    store = Store(tmp_path / "m.db")
    outcome = _run(
        box,
        store,
        _settings(),
        setups=_payload([_card_row("carry_trend")]),
        switch_path=_arm(tmp_path),
    )
    assert box.orders == []
    assert "did not report whether Algo Trading is on" in outcome.refused[0]["reason"]


def test_a_terminal_that_raises_on_the_question_places_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = _backend()

    def boom() -> Any:
        raise RuntimeError("terminal gone")

    monkeypatch.setattr(box, "terminal_info", boom)
    store = Store(tmp_path / "m.db")
    outcome = _run(
        box,
        store,
        _settings(),
        setups=_payload([_card_row("carry_trend")]),
        switch_path=_arm(tmp_path),
    )
    assert box.orders == []
    assert "would not report its state" in outcome.refused[0]["reason"]


def test_the_terminal_state_is_reported_while_the_switch_is_still_off(tmp_path: Path) -> None:
    """A panel that only admits Algo Trading is off *after* arming has already wasted the arming."""
    box = _backend(terminal_trade_allowed=False)
    store = Store(tmp_path / "m.db")
    outcome = _run(
        box,
        store,
        _settings(),
        setups=_payload([_card_row("carry_trend")]),
        switch_path=_arm(tmp_path, enabled=False),
        selection_path=_select(tmp_path, ["carry_trend"]),
    )
    assert box.orders == []
    assert outcome.terminal["trade_allowed"] is False
    assert "Algo Trading" in outcome.terminal["reason"]
    assert outcome.selection is not None and outcome.selection.strategies == ("carry_trend",)
    # Nothing is refused while the switch is off: it did not try to do anything.
    assert outcome.refused == []


def test_the_artifact_carries_the_registry_and_the_selection(tmp_path: Path) -> None:
    backend = _backend()
    store = Store(tmp_path / "m.db")
    registry = [
        {"family": "carry_trend", "timeframe": "H1", "status": "historically validated"},
        {"family": "daily_momentum", "timeframe": "H1", "status": "research candidate"},
    ]
    outcome = _run(
        backend,
        store,
        _settings(),
        setups=_payload([_card_row("carry_trend")]),
        switch_path=_arm(tmp_path),
        selection_path=_select(tmp_path, ["carry_trend"]),
        registry=registry,
    )
    payload = artifact(outcome, _settings(), market=_market(), now_epoch=NOW)
    assert payload["registry"] == registry
    assert payload["selection"]["strategies"] == ["carry_trend"]
    assert payload["terminal"] == {"trade_allowed": True, "reason": None}
