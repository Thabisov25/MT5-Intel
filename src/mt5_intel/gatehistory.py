"""Gate reason-code history: which codes keep appearing, and which ones ever clear.

The watcher journals every cycle's verdict and reason codes into the `recommendations`
table (D-028). A code list on its own cannot tell you whether a code is a market
condition that day or a check that is not wired up yet - `POSITIONS_UNVERIFIED` looks
identical either way. Counting, per code, how many journaled cycles it appeared in and
when it was last *absent* separates those two.

This module is pure on purpose: it turns aggregate hour buckets into the summary. The watcher
writes that summary to `dashboard/gate-history.json` (next to `live-status.json`) because
the dashboard has no SQLite driver, and `store.py` stays the only module that touches the
database file.

Two entry points, one assembler:

* `summarize_rollup(hours)` takes hour buckets - what the watcher reads from `gate_rollup`.
* `summarize(rows)` takes exact per-cycle rows, folds them into hour buckets first, and exists
  for callers that hold the raw cycles (tests, ad-hoc analysis) and as the reference the
  rollup path is checked against.

Both go through the same code path, so a bucket-shaped input cannot quietly answer differently
from a row-shaped one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .gate import CHECK_ORDER
from .gate_rollup import HOUR_S, UNPLACED_HOUR, roll_hours
from .sessions import SESSION_ORDER

WINDOW_DAYS = 90  # how far back a summary looks
MAX_CYCLES = 20000  # hard cap on raw rows summarised, so the artifact stays bounded
# Same bound expressed in the units the watcher now reads: a safety net behind retention, which is
# what actually keeps the window finite.
MAX_HOURS = WINDOW_DAYS * 24 + 24

# The order session buckets are listed in when first seen at the same time; unknown labels sort
# last so a new one shows up at the bottom rather than silently reshuffling the table.
_SESSION_SORT = {name: index for index, name in enumerate((*SESSION_ORDER, "off-hours"))}


def summarize(rows: list[dict[str, Any]], *, now: datetime | None = None) -> dict[str, Any]:
    """Summary from exact journal rows, oldest first. Folds them into hours, then assembles."""
    return summarize_rollup(roll_hours(rows[-MAX_CYCLES:]), now=now)


def summarize_rollup(
    hours: list[dict[str, Any]], *, now: datetime | None = None
) -> dict[str, Any]:
    """Per-code presence over journaled cycles, assembled from hour buckets, oldest first.

    `ever_cleared` is the one that answers the question: a code that appeared in *every*
    cycle since its first appearance has never produced a different answer, so it is
    either a check that cannot vary yet or a condition that is genuinely always true. A
    code that is absent from a later cycle has cleared at least once and therefore does
    track something.
    """
    generated = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    stats: dict[str, dict[str, Any]] = {}
    verdicts: dict[str, int] = {}
    per_hour: list[dict[str, Any]] = [{"hour": hour, "cycles": 0, "codes": {}} for hour in range(24)]
    per_session: dict[str, dict[str, Any]] = {}
    sessions_error: str | None = None
    unplaced_cycles = 0
    cycles = 0
    window_first: list[str] = []
    window_last: list[str] = []

    ordered = sorted(hours, key=lambda bucket: int(bucket["hour_epoch"]))[-MAX_HOURS:]
    newest = (
        set(max(ordered, key=lambda bucket: int(bucket.get("last_row_id") or 0))["newest_codes"])
        if ordered
        else set()
    )

    for bucket in ordered:
        epoch = int(bucket["hour_epoch"])
        bucket_cycles = int(bucket["cycles"])
        cycles_before = cycles
        cycles += bucket_cycles
        if bucket.get("first_ts"):
            window_first.append(str(bucket["first_ts"]))
        if bucket.get("last_ts"):
            window_last.append(str(bucket["last_ts"]))
        for verdict, count in (bucket.get("verdicts") or {}).items():
            verdicts[str(verdict)] = verdicts.get(str(verdict), 0) + int(count)

        if epoch == UNPLACED_HOUR:
            # Could not be placed in an hour at all. Counted, and never silently filed elsewhere.
            unplaced_cycles += bucket_cycles
        else:
            hour_slot = per_hour[(epoch % 86_400) // HOUR_S]
            hour_slot["cycles"] = int(hour_slot["cycles"]) + bucket_cycles
            for code, entry in (bucket.get("codes") or {}).items():
                present = int(entry.get("n") or 0)
                if present:
                    counts = hour_slot["codes"]
                    counts[code] = int(counts.get(code, 0)) + present

        if bucket.get("sessions_error"):
            sessions_error = str(bucket["sessions_error"])
        hour_of_day = None if epoch == UNPLACED_HOUR else (epoch % 86_400) // HOUR_S
        for label, data in (bucket.get("sessions") or {}).items():
            session = per_session.get(label)
            if session is None:
                session = per_session[label] = {
                    "session": label,
                    "cycles": 0,
                    "codes": {},
                    # The hours actually seen, not a first-to-last range: a bucket spans different
                    # UTC hours in summer and winter, so a range would claim hours it never covered.
                    "hours_seen": [],
                }
            session["cycles"] = int(session["cycles"]) + int(data.get("cycles") or 0)
            for code, count in (data.get("codes") or {}).items():
                session["codes"][code] = int(session["codes"].get(code, 0)) + int(count)
            if hour_of_day is not None and hour_of_day not in session["hours_seen"]:
                session["hours_seen"].append(hour_of_day)

        for code, entry in (bucket.get("codes") or {}).items():
            stat = stats.get(code)
            if stat is None:
                stat = stats[code] = {
                    "code": code,
                    "first_seen_ts": None,
                    "first_index": None,
                    "last_seen_ts": None,
                    "cycles_present": 0,
                    "last_cleared_ts": None,
                }
            present = int(entry.get("n") or 0)
            if present:
                if stat["first_seen_ts"] is None:
                    stat["first_seen_ts"] = entry.get("first")
                    # The cycle index of its first appearance, kept exact across hour boundaries by
                    # counting the cycles in the buckets before this one plus its offset inside it.
                    stat["first_index"] = cycles_before + int(entry.get("first_offset") or 0)
                stat["last_seen_ts"] = entry.get("last") or stat["last_seen_ts"]
                stat["cycles_present"] = int(stat["cycles_present"]) + present
            # An absence only counts once the code has appeared: before that it did not exist.
            if entry.get("last_absent") is not None and stat["first_seen_ts"] is not None:
                stat["last_cleared_ts"] = entry["last_absent"]

    for session in per_session.values():
        session["hours_seen"] = sorted(session["hours_seen"])
    session_buckets = sorted(
        per_session.values(),
        key=lambda entry: (
            int(entry["hours_seen"][0]) if entry["hours_seen"] else 24,
            _SESSION_SORT.get(str(entry["session"]), len(_SESSION_SORT)),
        ),
    )

    ordered_codes = [code for code in CHECK_ORDER if code in stats]
    ordered_codes += [code for code in stats if code not in CHECK_ORDER]
    codes: list[dict[str, Any]] = []
    for code in ordered_codes:
        entry = stats[code]
        if entry["first_seen_ts"] is None:
            continue
        since_first = cycles - int(entry["first_index"] or 0)
        present_count = int(entry["cycles_present"])
        codes.append(
            {
                "code": code,
                "first_seen_ts": entry["first_seen_ts"],
                "last_seen_ts": entry["last_seen_ts"],
                "cycles_present": present_count,
                "cycles_since_first_seen": since_first,
                "last_cleared_ts": entry["last_cleared_ts"],
                "in_latest_cycle": code in newest,
                "ever_cleared": present_count < since_first,
            }
        )

    return {
        "generated_at": generated,
        "cycles": cycles,
        "first_ts": min(window_first) if window_first else None,
        "last_ts": max(window_last) if window_last else None,
        "verdicts": verdicts,
        "codes_total": len(CHECK_ORDER),
        "never_seen": [code for code in CHECK_ORDER if code not in stats],
        "codes": codes,
        "by_hour": per_hour,
        "by_session": session_buckets,
        "sessions_available": sessions_error is None,
        "sessions_note": sessions_error,
        "unparsed_ts": unplaced_cycles,
        "window_days": WINDOW_DAYS,
    }
