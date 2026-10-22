"""The hourly rollup must not change what the summary says - only what it costs.

Rows are seeded straight into the journal because the public write path stamps `now`: testing a
window of several days needs rows with chosen timestamps.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mt5_intel import gate_rollup, gatehistory
from mt5_intel.store import Store

HOUR = 3600
BASE = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)  # a Monday


def _ts(hours: int, minute: int = 0) -> str:
    return (BASE + timedelta(hours=hours, minutes=minute)).isoformat(timespec="seconds")


def _seed(
    db: Path, rows: list[tuple[str, list[str]]], *, kind: str = "watch", bar_offset: int = 0
) -> None:
    """Bar epochs must be distinct: the journal dedupes on (symbol, bar_epoch, kind) and would
    silently ignore a second insert that reused one."""
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO recommendations(ts, bar_epoch, symbol, side, kind, reason_codes, verdict, payload)"
        " VALUES (?, ?, 'USDJPY', 'unknown', ?, ?, 'SKIP', '{}')",
        [
            (ts, bar_offset + index, kind, ",".join(codes))
            for index, (ts, codes) in enumerate(rows)
        ],
    )
    conn.commit()
    conn.close()


def _cycles_by_hour(store: Store) -> dict[int, int]:
    return {int(hour["hour_epoch"]): int(hour["cycles"]) for hour in store.gate_rollup_rows()}


def test_an_hour_keeps_the_per_code_detail_the_summary_needs(tmp_path: Path) -> None:
    db = tmp_path / "mt5.db"
    store = Store(db)
    _seed(
        db,
        [
            (_ts(0, 0), ["DATA_STALE"]),
            (_ts(0, 1), ["DATA_STALE", "NEWS_WINDOW"]),
            (_ts(0, 2), []),
        ],
    )
    rolled = gate_rollup.roll_hours(store.gate_raw_rows_after(0))

    assert len(rolled) == 1
    hour = rolled[0]
    assert hour["cycles"] == 3
    assert hour["verdicts"] == {"SKIP": 3}
    assert hour["first_ts"] == _ts(0, 0) and hour["last_ts"] == _ts(0, 2)
    assert hour["newest_codes"] == []
    stale = hour["codes"]["DATA_STALE"]
    assert stale["n"] == 2
    assert stale["first_offset"] == 0 and stale["first"] == _ts(0, 0)
    assert stale["last"] == _ts(0, 1)
    # Absent from the third cycle, and it already existed, so that cycle cleared it.
    assert stale["last_absent"] == _ts(0, 2)
    assert hour["codes"]["NEWS_WINDOW"]["n"] == 1
    assert hour["codes"]["NEWS_WINDOW"]["first_offset"] == 1


def test_a_cycle_with_no_codes_is_still_a_cycle(tmp_path: Path) -> None:
    db = tmp_path / "mt5.db"
    store = Store(db)
    _seed(db, [(_ts(0, m), []) for m in range(4)])
    hour = gate_rollup.roll_hours(store.gate_raw_rows_after(0))[0]
    assert hour["cycles"] == 4
    assert hour["codes"] == {}
    assert hour["newest_codes"] == []


def test_rolling_the_same_hour_again_does_not_double_count(tmp_path: Path) -> None:
    db = tmp_path / "mt5.db"
    store = Store(db)
    _seed(db, [(_ts(0, m), ["DATA_STALE"]) for m in range(5)])
    now = int((BASE + timedelta(hours=1)).timestamp())

    gate_rollup.refresh(store, now_epoch=now, window_days=90)
    first = store.gate_rollup_rows()
    gate_rollup.refresh(store, now_epoch=now, window_days=90)
    second = store.gate_rollup_rows()

    assert _cycles_by_hour(store) == {int(hour["hour_epoch"]): 5 for hour in first}
    assert [hour["codes"] for hour in second] == [hour["codes"] for hour in first]
    assert [hour["last_row_id"] for hour in second] == [hour["last_row_id"] for hour in first]


def test_refresh_reads_one_hour_once_the_backfill_is_done(tmp_path: Path) -> None:
    """The point of the rollup: cost per refresh is one hour, not the whole window."""
    db = tmp_path / "mt5.db"
    store = Store(db)
    rows = [(_ts(offset, minute), ["DATA_STALE"]) for offset in range(60) for minute in range(60)]
    _seed(db, rows)
    now = int((BASE + timedelta(hours=60)).timestamp())

    backfill = gate_rollup.refresh(store, now_epoch=now, window_days=90)
    assert backfill["rows_read"] == len(rows)  # one-off: everything is unrolled to begin with
    assert backfill["hours_written"] == 60

    steady = gate_rollup.refresh(store, now_epoch=now, window_days=90)
    assert steady["hours_written"] == 1
    assert steady["rows_read"] == 60  # the newest hour only
    assert sum(_cycles_by_hour(store).values()) == len(rows)  # and nothing was lost


def test_retention_drops_hours_that_leave_the_window(tmp_path: Path) -> None:
    db = tmp_path / "mt5.db"
    store = Store(db)
    _seed(db, [(_ts(offset, minute), ["SPREAD_TOO_WIDE"]) for offset in range(72) for minute in range(10)])
    now = int((BASE + timedelta(hours=72)).timestamp())

    stats = gate_rollup.refresh(store, now_epoch=now, window_days=2)
    assert stats["pruned_hours"] == 24  # day 0 falls outside a two-day window
    kept = store.gate_rollup_rows()
    assert min(int(hour["hour_epoch"]) for hour in kept) >= int((BASE + timedelta(days=1)).timestamp())
    assert sum(_cycles_by_hour(store).values()) == 48 * 10


def test_a_cycle_that_cannot_be_placed_is_reported_not_dropped(tmp_path: Path) -> None:
    db = tmp_path / "mt5.db"
    store = Store(db)
    _seed(db, [("not-a-timestamp", ["DATA_STALE"]), (_ts(0, 0), ["NEWS_WINDOW"])])

    stats = gate_rollup.refresh(store, now_epoch=int(BASE.timestamp()) + HOUR, window_days=90)
    summary = gatehistory.summarize_rollup(store.gate_rollup_rows())

    assert stats["unplaced_cycles"] == 1
    assert summary["unparsed_ts"] == 1
    assert summary["cycles"] == 2  # it still counts as a cycle
    assert {code["code"] for code in summary["codes"]} == {"DATA_STALE", "NEWS_WINDOW"}
    assert sum(hour["cycles"] for hour in summary["by_hour"]) == 1  # but it is in no hour


def test_a_back_dated_row_cannot_truncate_an_hour_already_folded(tmp_path: Path) -> None:
    db = tmp_path / "mt5.db"
    store = Store(db)
    _seed(db, [(_ts(0, m), ["DATA_STALE"]) for m in range(3)] + [(_ts(1, 0), ["NEWS_WINDOW"])])
    now = int((BASE + timedelta(hours=2)).timestamp())
    gate_rollup.refresh(store, now_epoch=now, window_days=90)
    assert _cycles_by_hour(store) == {int(BASE.timestamp()): 3, int(BASE.timestamp()) + HOUR: 1}

    # A row whose id is newest but whose timestamp points back inside hour 0. Rebuilding hour 0
    # from this fragment would lose two cycles, so it must not be written.
    _seed_size = len(store.gate_rollup_rows())
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO recommendations(ts, bar_epoch, symbol, side, kind, reason_codes, verdict, payload)"
        " VALUES (?, 99, 'USDJPY', 'unknown', 'watch', 'SPREAD_TOO_WIDE', 'SKIP', '{}')",
        (_ts(0, 9),),
    )
    conn.commit()
    conn.close()

    stats = gate_rollup.refresh(store, now_epoch=now, window_days=90)
    assert stats["unplaced_cycles"] == 1
    assert len(store.gate_rollup_rows()) == _seed_size + 1  # the unplaced bucket, not a rewrite of 0
    assert _cycles_by_hour(store)[int(BASE.timestamp())] == 3  # hour 0 still whole
    # The cycle is still counted and still reported, just not filed under an hour it did not belong to.
    summary = gatehistory.summarize_rollup(store.gate_rollup_rows())
    assert summary["unparsed_ts"] == 1
    assert summary["cycles"] == 5
    assert "SPREAD_TOO_WIDE" in {code["code"] for code in summary["codes"]}


def test_a_quiet_hour_clears_a_code_that_only_earlier_hours_saw(tmp_path: Path) -> None:
    """Across separate refreshes the folder must carry codes forward, or an hour that mentions
    nothing would look like it had never seen the code at all."""
    db = tmp_path / "mt5.db"
    store = Store(db)
    _seed(db, [(_ts(0, 0), ["DATA_STALE"]), (_ts(1, 0), ["NEWS_WINDOW"])])
    now = int((BASE + timedelta(hours=2)).timestamp())
    gate_rollup.refresh(store, now_epoch=now, window_days=90)

    # A third hour with no codes at all. It is rolled in its own refresh, so the only way it can
    # know DATA_STALE existed is by carrying it forward from the hours below it.
    _seed(db, [(_ts(2, 0), [])], bar_offset=100)
    gate_rollup.refresh(store, now_epoch=now, window_days=90)

    summary = gatehistory.summarize_rollup(store.gate_rollup_rows())
    stale = next(code for code in summary["codes"] if code["code"] == "DATA_STALE")
    assert summary["cycles"] == 3
    assert stale["cycles_present"] == 1
    assert stale["cycles_since_first_seen"] == 3
    assert stale["ever_cleared"] is True
    assert stale["last_cleared_ts"] == _ts(2, 0)
    assert stale["in_latest_cycle"] is False


def test_summary_from_the_rollup_matches_the_raw_path(tmp_path: Path) -> None:
    """Differential test: for the same journal, both entry points must produce the same summary."""
    db = tmp_path / "mt5.db"
    store = Store(db)
    rows: list[tuple[str, list[str]]] = []
    for offset in range(60):
        for minute in range(30):
            codes: list[str] = []
            if offset % 7 == 0:
                codes.append("SPREAD_TOO_WIDE")
            if offset % 11 == 0:
                codes.append("DATA_STALE")
            if minute % 3 == 0:
                codes.append("NO_VALIDATED_STRATEGY")
            if offset % 5 == 0 and minute == 7:
                codes.append("NEWS_WINDOW")
            rows.append((_ts(offset, minute), codes))
    _seed(db, rows)

    gate_rollup.refresh(
        store, now_epoch=int((BASE + timedelta(hours=60)).timestamp()), window_days=90
    )
    rolled = store.gate_rollup_rows()
    raw = store.gate_history_rows()

    from_rollup = gatehistory.summarize_rollup(rolled)
    from_rows = gatehistory.summarize(raw)
    from_rollup.pop("generated_at")
    from_rows.pop("generated_at")

    assert from_rollup == from_rows
    assert from_rows["cycles"] == len(rows)
    assert len(rolled) == 60  # the summary reads hours...
    assert len(raw) == 1800  # ...not the 1,800 cycles behind them


def test_rollup_size_follows_hours_not_the_cycle_cadence(tmp_path: Path) -> None:
    """Ten times the cycles in the same hours must not mean ten times the reading."""
    sizes: list[int] = []
    for per_hour in (5, 50):
        db = tmp_path / f"mt5-{per_hour}.db"
        store = Store(db)
        _seed(db, [(_ts(offset, minute), ["DATA_STALE"]) for offset in range(12) for minute in range(per_hour)])
        gate_rollup.refresh(
            store, now_epoch=int((BASE + timedelta(hours=12)).timestamp()), window_days=90
        )
        sizes.append(len(store.gate_rollup_rows()))
        assert sum(_cycles_by_hour(store).values()) == 12 * per_hour
    assert sizes == [12, 12]
