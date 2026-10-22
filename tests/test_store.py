from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mt5_intel.store import Store


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "mt5.db")


def test_reopen_keeps_data(store: Store) -> None:
    """A reopened Store must serve previously journaled events (schema + persistence)."""
    store.append_event("cycle", {"n": 1})
    reopened = Store(store.path)
    assert reopened.recent_events("cycle")[0]["payload"] == {"n": 1}


def test_recommendation_unique_per_symbol_bar_side(store: Store) -> None:
    first = store.add_recommendation(100, "USDJPY", "long", ["A"], "SKIP", {"x": 1})
    second = store.add_recommendation(100, "USDJPY", "long", ["A"], "SKIP", {"x": 1})
    assert first == second  # conflict ignored, same row kept
    third = store.add_recommendation(160, "USDJPY", "long", ["A"], "SKIP", {"x": 1})
    assert third != first
    assert len(store.recent_recommendations()) == 2


def test_decisions_are_appended_per_recommendation(store: Store) -> None:
    rec = store.add_recommendation(100, "USDJPY", "short", [], "CARD", {})
    store.record_decision(rec, "accepted", "looks good")
    store.record_decision(rec, "rejected", "changed mind")
    rows = store.decisions_for(rec)
    assert [r["decision"] for r in rows] == ["accepted", "rejected"]


def test_equity_baseline_is_recorded_once_per_day(store: Store) -> None:
    """A day's opening equity is recorded once; later equity for that day cannot rewrite it."""
    assert store.equity_baseline("2026-10-12") is None
    store.set_equity_baseline("2026-10-12", 1000.0, 1000.0)
    store.set_equity_baseline("2026-10-12", 500.0, 500.0)
    baseline = store.equity_baseline("2026-10-12")
    assert baseline is not None
    assert baseline["equity"] == 1000.0
    assert baseline["balance"] == 1000.0
    assert baseline["ts"]
    assert store.equity_baseline("2026-10-13") is None


def test_equity_baseline_is_scoped_to_the_account(store: Store) -> None:
    """One account's opening equity is never the yardstick for another account.

    The journal outlives a login, so a baseline the previous account recorded must not be read
    by the new one - and the account that is actually running takes the day over.
    """
    store.set_equity_baseline("2026-10-13", 3.03, 3.03, 49869754)
    assert store.equity_baseline("2026-10-13", 49869754) is not None
    # A different login reads nothing, and an unknown owner reads nothing here.
    assert store.equity_baseline("2026-10-13", 49999999) is None
    assert store.equity_baseline("2026-10-13") is None

    store.set_equity_baseline("2026-10-13", 1_000_054.77, 1_000_054.77, 49999999)
    taken_over = store.equity_baseline("2026-10-13", 49999999)
    assert taken_over is not None and taken_over["equity"] == 1_000_054.77
    assert taken_over["account"] == 49999999
    assert store.equity_baseline("2026-10-13", 49869754) is None

    # The account that owns the day cannot move its own baseline afterwards.
    store.set_equity_baseline("2026-10-13", 500.0, 500.0, 49999999)
    still_owned = store.equity_baseline("2026-10-13", 49999999)
    assert still_owned is not None and still_owned["equity"] == 1_000_054.77


def test_a_journal_without_the_account_column_is_migrated(tmp_path: Path) -> None:
    """A database written by an earlier build must gain the column instead of failing to open."""
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE equity_days (day TEXT PRIMARY KEY, equity REAL NOT NULL,"
            " balance REAL NOT NULL, ts TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO equity_days(day, equity, balance, ts) VALUES (?, ?, ?, ?)",
            ("2026-10-13", 3.03, 3.03, "2026-10-12T22:00:59+00:00"),
        )

    store = Store(path)
    # The pre-existing row is unowned: it matches neither a named account nor a fresh baseline read.
    assert store.equity_baseline("2026-10-13", 49999999) is None
    store.set_equity_baseline("2026-10-13", 1_000_054.77, 1_000_054.77, 49999999)
    migrated = store.equity_baseline("2026-10-13", 49999999)
    assert migrated is not None and migrated["equity"] == 1_000_054.77


def test_a_journal_with_the_old_auto_trades_shape_is_migrated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first start after the multi-symbol upgrade must migrate, not fail to open.

    The crash this guards against came from the order of operations: applying _SCHEMA before
    migrating left `CREATE TABLE IF NOT EXISTS auto_trades` a no-op on the old-shape table and
    then let its new symbol index fail with `no such column: symbol` before the rebuild ran.
    An actual database this build shipped against is reproduced here - old table, old index,
    rows already in it - so a mid-flight database cannot die on start again.
    """
    monkeypatch.setenv("SYMBOL_ROOT", "USDJPY")
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE auto_trades ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, strategy TEXT NOT NULL, families TEXT NOT NULL,"
            " side TEXT NOT NULL, signal_epoch INTEGER NOT NULL, signal_utc TEXT, status TEXT NOT NULL,"
            " reason TEXT, ticket INTEGER, entry REAL, sl REAL, tp REAL, volume REAL,"
            " stop_pips REAL, tp_r REAL, risk_amount REAL, equity REAL, risk_pct REAL,"
            " digits INTEGER, retcode INTEGER, broker_comment TEXT, magic INTEGER,"
            " expires_epoch INTEGER, code_version TEXT NOT NULL, open_ts TEXT NOT NULL,"
            " closed_ts TEXT, close_reason TEXT, close_ticket INTEGER,"
            " UNIQUE(strategy, signal_epoch, side))"
        )
        conn.execute("CREATE INDEX idx_auto_status ON auto_trades(status)")
        conn.execute(
            "INSERT INTO auto_trades(strategy, families, side, signal_epoch, status, code_version, open_ts)"
            " VALUES ('carry_trend', '[\"carry_trend\"]', 'buy', 1792393200, 'placed', 'auto-v1', ?)",
            ("2026-10-14T03:37:03+00:00",),
        )

    store = Store(path)

    # The old row survived and now names the primary symbol, so the cap sees it at all.
    rows = store.auto_trades_open()
    assert [row["symbol"] for row in rows] == ["USDJPY"]
    assert rows[0]["strategy"] == "carry_trend" and rows[0]["side"] == "buy"
    # And a second symbol claiming the same signal bar is its own claim, not a spent dedup:
    # the migrated row can only speak for the symbol it was backfilled to name.
    assert store.auto_trade_taken("carry_trend", 1792393200, "buy", "USDJPY") is True
    assert store.auto_trade_taken("carry_trend", 1792393200, "buy", "EURUSD") is False
    # The new index exists, so the per-symbol open-position read has something to use.
    with sqlite3.connect(path, timeout=10) as conn:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'auto_trades'"
            )
        }
    assert "idx_auto_symbol_status" in names


def test_strategy_statuses_lists_every_family_not_only_the_validated_ones(store: Store) -> None:
    """The panel needs the whole registry to offer a choice, and each family's standing with it."""
    assert store.strategy_statuses() == []
    store.set_strategy_status("carry_trend", "H1", "historically validated", {"n": 468})
    store.set_strategy_status("daily_momentum", "H1", "research candidate", {"n": 714, "mean_r": 0.044})
    rows = store.strategy_statuses()
    assert [row["family"] for row in rows] == ["carry_trend", "daily_momentum"]
    assert {row["family"]: row["status"] for row in rows} == {
        "carry_trend": "historically validated",
        "daily_momentum": "research candidate",
    }
    # Evidence comes back parsed, because it is what the panel quotes next to the name.
    assert rows[1]["evidence"]["mean_r"] == 0.044
    # And the validated-only reader is unchanged: it is what the gate asks.
    assert [row["family"] for row in store.validated_strategies()] == ["carry_trend"]


def test_a_registry_row_with_unreadable_evidence_is_still_listed(tmp_path: Path) -> None:
    """One corrupt row must not hide the rest of the registry from the panel."""
    store = Store(tmp_path / "mt5.db")
    store.set_strategy_status("carry_trend", "H1", "research candidate", {"n": 1})
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE strategies SET evidence = ? WHERE family = ?", ("{ not json", "carry_trend"))
    rows = store.strategy_statuses()
    assert rows == [
        {
            "family": "carry_trend",
            "timeframe": "H1",
            "status": "research candidate",
            "evidence": {},
            "updated_ts": rows[0]["updated_ts"],
        }
    ]


def test_notification_dedupe_within_cooldown(store: Store) -> None:
    assert store.should_notify("gate_skip:blocked by X") is True
    assert store.should_notify("gate_skip:blocked by X") is False
    assert store.should_notify("gate_skip:different reason") is True


def test_notification_cooldown_expires(store: Store) -> None:
    signature = "stale:old data"
    assert store.should_notify(signature) is True
    # Simulate cooldown passing by backdating the stored notification event.
    old = (datetime.now(UTC) - timedelta(hours=1)).isoformat(timespec="seconds")
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE events SET ts = ? WHERE kind = 'notify'", (old,))
    assert store.should_notify(signature) is True
