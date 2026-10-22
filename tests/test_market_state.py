from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from mt5_intel import watch
from mt5_intel.gate import GateInputs, evaluate
from mt5_intel.store import Store


def test_gate_skip_is_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = Store(tmp_path / "mt5.db")
    gate = evaluate(GateInputs())
    calls: list[tuple[str, ...]] = []

    def fake_notify(store_arg: Store, kind: str, title: str, message: str) -> None:
        calls.append((kind, title, message))

    monkeypatch.setattr(watch, "notify_alert", fake_notify)
    watch._notify_state_changes(store, connected=True, gate=gate, bar_age=30, spread=1.4)
    assert calls == []


def test_stale_and_disconnect_still_alert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = Store(tmp_path / "mt5.db")
    gate = evaluate(GateInputs())
    calls: list[tuple[str, ...]] = []

    def fake_notify(store_arg: Store, kind: str, title: str, message: str) -> None:
        calls.append((kind, message))

    monkeypatch.setattr(watch, "notify_alert", fake_notify)
    watch._notify_state_changes(store, connected=False, gate=gate, bar_age=None, spread=None)
    watch._notify_state_changes(store, connected=True, gate=gate, bar_age=999, spread=1.4)
    kinds = [c[0] for c in calls]
    assert "disconnect" in kinds
    assert "stale" in kinds


def test_market_transition_alerts_fire_once(tmp_path: Path) -> None:
    """Open->closed->open cycles produce exactly one open and one closed alert (dedupe)."""
    store = Store(tmp_path / "mt5.db")

    def go(state: str) -> None:
        if state == "open":
            watch.notify_alert(store, "market_open", "Market open", "USD/JPY session started.")
        else:
            watch.notify_alert(store, "market_closed", "Market closed", "Idle until reopen.")

    go("open")
    go("open")  # same signature within cooldown -> suppressed
    go("closed")
    go("closed")
    go("open")  # within 30-min cooldown -> suppressed (by design)
    events = store.recent_events("notify", limit=10)
    kinds = [e["payload"].get("kind") for e in events]
    assert kinds.count("market_open") == 1
    assert kinds.count("market_closed") == 1


def test_idle_status_shape(tmp_path: Path) -> None:
    status_path = tmp_path / "live-status.json"
    watch.STATUS_FILE = status_path
    watch._atomic_write_json(
        status_path,
        {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "watcher_alive": True,
            "mode": "idle",
            "market_state": "closed",
            "heartbeat_ttl_s": watch.HEARTBEAT_TTL_IDLE_S,
        },
    )
    loaded = _read_json(status_path)
    assert loaded["mode"] == "idle"
    assert loaded["heartbeat_ttl_s"] == 900


def _read_json(path: Path) -> dict[str, Any]:
    import json

    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded
