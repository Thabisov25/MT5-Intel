from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

import pytest

from mt5_intel import gatehistory, watch
from mt5_intel.gate import CHECK_ORDER
from mt5_intel.store import Store

# Codes that are structurally unmeasurable today (no positions API, no risk sizing, no
# fast-move measurement, nothing validated on the watcher's timeframe). They are here to
# make the "never cleared" case concrete, not to assert they stay that way.
STUCK = ("FAST_MOVE_CIRCUIT_BREAKER", "POSITIONS_UNVERIFIED", "NO_VALIDATED_STRATEGY", "RISK_FIT_UNVERIFIED")


def _row(ts: str, codes: tuple[str, ...], verdict: str = "SKIP") -> dict[str, object]:
    return {"ts": ts, "verdict": verdict, "reason_codes": list(codes)}


def _stamp(minute: int) -> str:
    return f"2026-10-12T11:{minute:02d}:00+00:00"


def test_code_present_in_every_cycle_never_clears() -> None:
    rows = [_row(_stamp(0), STUCK), _row(_stamp(1), STUCK), _row(_stamp(2), STUCK)]
    summary = gatehistory.summarize(rows)
    assert summary["cycles"] == 3
    assert summary["verdicts"] == {"SKIP": 3}
    fast_move = next(c for c in summary["codes"] if c["code"] == "FAST_MOVE_CIRCUIT_BREAKER")
    assert fast_move["cycles_present"] == 3
    assert fast_move["cycles_since_first_seen"] == 3
    assert fast_move["ever_cleared"] is False
    assert fast_move["last_cleared_ts"] is None
    assert fast_move["in_latest_cycle"] is True


def test_code_that_disappears_has_cleared() -> None:
    rows = [
        _row(_stamp(0), ("DATA_STALE",) + STUCK),
        _row(_stamp(1), ("DATA_STALE",) + STUCK),
        _row(_stamp(2), STUCK),
        _row(_stamp(3), STUCK),
    ]
    summary = gatehistory.summarize(rows)
    stale = next(c for c in summary["codes"] if c["code"] == "DATA_STALE")
    assert stale["cycles_present"] == 2
    assert stale["cycles_since_first_seen"] == 4
    assert stale["ever_cleared"] is True
    assert stale["last_cleared_ts"] == _stamp(3)  # last cycle it was absent
    assert stale["first_seen_ts"] == _stamp(0)
    assert stale["in_latest_cycle"] is False


def test_code_that_returns_is_still_marked_as_clearing() -> None:
    rows = [
        _row(_stamp(0), ("SPREAD_TOO_WIDE",) + STUCK),
        _row(_stamp(1), STUCK),
        _row(_stamp(2), ("SPREAD_TOO_WIDE",) + STUCK),
    ]
    summary = gatehistory.summarize(rows)
    spread = next(c for c in summary["codes"] if c["code"] == "SPREAD_TOO_WIDE")
    assert spread["cycles_present"] == 2
    assert spread["ever_cleared"] is True
    assert spread["last_cleared_ts"] == _stamp(1)  # the newest cycle it was missing from
    assert spread["in_latest_cycle"] is True


def test_cost_of_a_code_is_measured_from_its_own_first_appearance() -> None:
    """A code introduced late cannot claim the cycles before it existed as 'not cleared'."""
    rows = [_row(_stamp(0), STUCK), _row(_stamp(1), STUCK), _row(_stamp(2), STUCK + ("NEWS_WINDOW",))]
    summary = gatehistory.summarize(rows)
    news = next(c for c in summary["codes"] if c["code"] == "NEWS_WINDOW")
    assert news["first_seen_ts"] == _stamp(2)
    assert news["cycles_present"] == 1
    assert news["cycles_since_first_seen"] == 1
    assert news["ever_cleared"] is False
    assert news["in_latest_cycle"] is True


def test_ordering_and_never_seen_come_from_check_order() -> None:
    rows = [_row(_stamp(0), ("RISK_FIT_UNVERIFIED", "DATA_STALE"))]
    summary = gatehistory.summarize(rows)
    assert [c["code"] for c in summary["codes"]] == ["DATA_STALE", "RISK_FIT_UNVERIFIED"]
    assert summary["codes_total"] == len(CHECK_ORDER)
    assert summary["never_seen"] == [c for c in CHECK_ORDER if c not in {"DATA_STALE", "RISK_FIT_UNVERIFIED"}]
    assert "MARKET_CLOSED" in summary["never_seen"]


def test_duplicate_code_in_one_row_counted_once() -> None:
    rows = [_row(_stamp(0), ("DATA_STALE", "DATA_STALE"))]
    summary = gatehistory.summarize(rows)
    assert summary["codes"][0]["cycles_present"] == 1


def test_empty_journal_reports_nothing_seen() -> None:
    summary = gatehistory.summarize([])
    assert summary["cycles"] == 0
    assert summary["codes"] == []
    assert summary["first_ts"] is None
    assert summary["last_ts"] is None
    assert summary["verdicts"] == {}
    assert summary["never_seen"] == list(CHECK_ORDER)


def _row_at(iso: str, codes: tuple[str, ...]) -> dict[str, object]:
    return {"ts": iso, "verdict": "SKIP", "reason_codes": list(codes)}


def test_counts_stay_exact_across_hour_boundaries() -> None:
    """The summary now assembles from hour buckets, so these must survive spanning two hours."""
    rows = [
        _row_at("2026-10-12T10:55:00+00:00", ("SPREAD_TOO_WIDE",)),
        _row_at("2026-10-12T11:05:00+00:00", ("SPREAD_TOO_WIDE",)),
        _row_at("2026-10-12T11:35:00+00:00", ()),
        _row_at("2026-10-12T11:55:00+00:00", ("SPREAD_TOO_WIDE",)),
    ]
    summary = gatehistory.summarize(rows)
    spread = next(c for c in summary["codes"] if c["code"] == "SPREAD_TOO_WIDE")
    assert spread["first_seen_ts"] == "2026-10-12T10:55:00+00:00"
    assert spread["last_seen_ts"] == "2026-10-12T11:55:00+00:00"
    assert spread["cycles_present"] == 3
    assert spread["cycles_since_first_seen"] == 4
    assert spread["ever_cleared"] is True
    assert spread["last_cleared_ts"] == "2026-10-12T11:35:00+00:00"
    assert spread["in_latest_cycle"] is True


def test_an_absence_in_a_later_hour_is_the_one_recorded() -> None:
    rows = [
        _row_at("2026-10-12T10:05:00+00:00", ("NEWS_WINDOW",)),
        _row_at("2026-10-12T11:05:00+00:00", ()),
        _row_at("2026-10-12T11:35:00+00:00", ()),
    ]
    summary = gatehistory.summarize(rows)
    news = next(c for c in summary["codes"] if c["code"] == "NEWS_WINDOW")
    assert news["cycles_present"] == 1
    assert news["cycles_since_first_seen"] == 3
    assert news["ever_cleared"] is True
    assert news["last_cleared_ts"] == "2026-10-12T11:35:00+00:00"
    assert news["in_latest_cycle"] is False


def test_latest_cycle_comes_from_the_newest_hour() -> None:
    rows = [
        _row_at("2026-10-12T10:05:00+00:00", ("DATA_STALE",)),
        _row_at("2026-10-12T11:05:00+00:00", ()),
    ]
    summary = gatehistory.summarize(rows)
    stale = next(c for c in summary["codes"] if c["code"] == "DATA_STALE")
    assert stale["in_latest_cycle"] is False  # the newest cycle is the quiet one


def test_hour_breakdown_counts_every_cycle_as_a_denominator() -> None:
    """A cycle with no reason codes still counts, or the per-hour rate would read too high."""
    rows = [
        _row_at("2026-10-12T11:05:00+00:00", ("SPREAD_TOO_WIDE",)),
        _row_at("2026-10-12T11:35:00+00:00", ("SPREAD_TOO_WIDE",)),
        _row_at("2026-10-12T11:55:00+00:00", ()),
        _row_at("2026-10-12T14:05:00+00:00", ("DATA_STALE",)),
    ]
    summary = gatehistory.summarize(rows)
    hours = {h["hour"]: h for h in summary["by_hour"]}
    assert len(summary["by_hour"]) == 24
    assert hours[11]["cycles"] == 3
    assert hours[11]["codes"] == {"SPREAD_TOO_WIDE": 2}
    assert hours[14]["cycles"] == 1 and hours[14]["codes"] == {"DATA_STALE": 1}
    assert hours[3]["cycles"] == 0 and hours[3]["codes"] == {}


def test_overlap_is_one_bucket_and_cycles_are_not_double_counted() -> None:
    rows = [
        _row_at("2026-10-12T10:05:00+00:00", ("SPREAD_TOO_WIDE",)),  # London only (BST)
        _row_at("2026-10-12T14:05:00+00:00", ("SPREAD_TOO_WIDE",)),  # London + New York
        _row_at("2026-10-12T22:05:00+00:00", ("SPREAD_TOO_WIDE",)),  # off-hours
    ]
    summary = gatehistory.summarize(rows)
    assert summary["sessions_available"] is True
    by_session = {s["session"]: s for s in summary["by_session"]}
    assert set(by_session) == {"London", "London + New York", "off-hours"}
    assert sum(s["cycles"] for s in summary["by_session"]) == len(rows)
    assert by_session["London"]["cycles"] == 1
    assert by_session["off-hours"]["codes"] == {"SPREAD_TOO_WIDE": 1}
    # Ordered by the hour the bucket first appears in.
    assert [s["session"] for s in summary["by_session"]] == [
        "London",
        "London + New York",
        "off-hours",
    ]
    assert by_session["London + New York"]["hours_seen"] == [14]
    assert by_session["London"]["hours_seen"] == [10]
    assert by_session["off-hours"]["hours_seen"] == [22]


def test_session_bucket_records_the_hours_it_was_seen_in() -> None:
    """Hours, not a range: the same bucket covers different UTC hours in summer and winter."""
    rows = [
        _row_at("2026-10-12T09:05:00+00:00", ("SPREAD_TOO_WIDE",)),
        _row_at("2026-10-12T11:05:00+00:00", ()),
        _row_at("2026-10-12T09:35:00+00:00", ()),
    ]
    summary = gatehistory.summarize(rows)
    london = next(s for s in summary["by_session"] if s["session"] == "London")
    assert london["hours_seen"] == [9, 11]
    assert london["cycles"] == 3
    assert london["codes"] == {"SPREAD_TOO_WIDE": 1}


def test_session_bucket_depends_on_the_date_not_only_the_utc_hour() -> None:
    """The whole point of a local-clock definition: 07:30 UTC is a different session in July."""
    winter = gatehistory.summarize([_row_at("2026-01-15T07:30:00+00:00", ("SPREAD_TOO_WIDE",))])
    summer = gatehistory.summarize([_row_at("2026-07-15T07:30:00+00:00", ("SPREAD_TOO_WIDE",))])
    assert [s["session"] for s in winter["by_session"]] == ["Tokyo"]
    assert [s["session"] for s in summer["by_session"]] == ["Tokyo + London"]


def test_missing_timezone_data_is_reported_while_the_hour_profile_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An environment gap must be named, and must not take the hour breakdown down with it."""

    def boom(_key: str) -> None:
        raise ZoneInfoNotFoundError("no tz database")

    monkeypatch.setattr("mt5_intel.sessions.ZoneInfo", boom)
    summary = gatehistory.summarize([_row_at("2026-10-12T11:05:00+00:00", ("SPREAD_TOO_WIDE",))])
    assert summary["sessions_available"] is False
    assert "tzdata" in str(summary["sessions_note"])
    assert summary["by_session"] == []
    assert {h["hour"]: h["cycles"] for h in summary["by_hour"]}[11] == 1


def test_unparsable_timestamp_is_counted_not_silently_dropped() -> None:
    summary = gatehistory.summarize([_row_at("not-a-timestamp", ("DATA_STALE",))])
    assert summary["unparsed_ts"] == 1
    assert summary["cycles"] == 1
    assert summary["codes"][0]["cycles_present"] == 1
    assert all(h["cycles"] == 0 for h in summary["by_hour"])


def test_store_rows_are_chronological_windowed_and_watch_only(tmp_path: Path) -> None:
    store = Store(tmp_path / "mt5.db")
    store.add_recommendation(
        bar_epoch=1, symbol="USDJPY", side="unknown", reason_codes=["DATA_STALE"],
        verdict="SKIP", payload={"kind": "watch", "note": "older"},
    )
    store.add_recommendation(
        bar_epoch=2, symbol="USDJPY", side="unknown", reason_codes=["NEWS_WINDOW"],
        verdict="SKIP", payload={"kind": "watch"},
    )
    # Not a watcher cycle: it must not pollute the watch summary.
    store.add_recommendation(
        bar_epoch=3, symbol="USDJPY", side="unknown", reason_codes=["MARKET_CLOSED"],
        verdict="SKIP", payload={"kind": "card"},
    )

    rows = store.gate_history_rows()
    assert [r["reason_codes"] for r in rows] == [["DATA_STALE"], ["NEWS_WINDOW"]]
    assert rows[0]["verdict"] == "SKIP"

    # Since_ts is inclusive, so the window has to start strictly after the newest row.
    future = (datetime.now(UTC) + timedelta(minutes=1)).isoformat(timespec="seconds")
    assert store.gate_history_rows(since_ts=future) == []


def test_watcher_writes_the_artifact_and_throttles_rewrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = Store(tmp_path / "mt5.db")
    store.add_recommendation(
        bar_epoch=1, symbol="USDJPY", side="unknown", reason_codes=list(STUCK),
        verdict="SKIP", payload={},
    )
    artifact = tmp_path / "gate-history.json"
    monkeypatch.setattr(watch, "GATE_HISTORY_FILE", artifact)

    now_epoch = int(time.time())
    watch._refresh_gate_history(store, now_epoch)
    first = json.loads(artifact.read_text(encoding="utf-8"))
    assert first["cycles"] == 1
    assert [c["code"] for c in first["codes"]] == list(STUCK)

    # Inside the refresh window the artifact is left alone, even though the journal moved on.
    store.add_recommendation(
        bar_epoch=2, symbol="USDJPY", side="unknown", reason_codes=list(STUCK),
        verdict="SKIP", payload={},
    )
    watch._refresh_gate_history(store, now_epoch + 10)
    assert json.loads(artifact.read_text(encoding="utf-8")) == first

    # Past the window it is rewritten, and now sees both cycles.
    watch._refresh_gate_history(store, now_epoch + watch.GATE_HISTORY_REFRESH_S + 1)
    second = json.loads(artifact.read_text(encoding="utf-8"))
    assert second["cycles"] == 2
