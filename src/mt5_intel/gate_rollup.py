"""Fold the watch journal into hourly buckets so the 90-day summary stops reading every cycle.

The summary is rebuilt every few minutes from a 90-day window. Reading raw cycles for it is linear
in the journal (about 2,880 cycles a day at the watcher's 30 s cadence), so its cost grows forever
even though the window does not. This module folds cycles into one row per UTC hour - with the
per-code detail the summary actually needs, not just totals - and prunes hours that age out of the
window. The summary then reads at most `WINDOW_DAYS * 24` rows whatever the cadence is.

Two properties keep the folding honest:

* The newest hour is always **rebuilt** from its own rows rather than merged into, so a re-run
  (every refresh, and after any crash) cannot double-count it.
* A cycle that cannot be placed in an hour - its timestamp did not parse, or it arrived out of
  order - goes to an explicit bucket instead of being dropped or quietly filed under the nearest
  hour. The summary reports how many landed there.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .sessions import SessionsUnavailable, session_label
from .store import Store

HOUR_S = 3600
# A cycle placed in no hour. Not a real hour: it exists so nothing is silently lost.
UNPLACED_HOUR = -1


def parse_ts_epoch(ts: str) -> int | None:
    """Epoch seconds for a journal timestamp, or None when it cannot be read."""
    try:
        return int(datetime.fromisoformat(str(ts)).timestamp())
    except ValueError:
        return None


def hour_start(epoch_s: int) -> int:
    return epoch_s // HOUR_S * HOUR_S


def _code_entry() -> dict[str, Any]:
    """Per-code detail inside one hour. `first_offset` is the cycle index within the hour, which
    is what lets the summary keep counting 'cycles since this code first appeared' exactly."""
    return {"n": 0, "first_offset": None, "first": None, "last": None, "last_absent": None}


def roll_hours(
    rows: list[dict[str, Any]],
    *,
    known_codes: set[str] | None = None,
    unplaced_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Aggregate per-cycle rows (oldest first) into one bucket per UTC hour.

    `known_codes` are codes that already existed in *earlier* hours: an hour where such a code is
    absent has just cleared it, and that fact belongs to this hour's bucket. Within an hour this is
    tracked from the rows themselves, so cycles before a code's first appearance are not counted as
    absences.

    `unplaced_ids` are rows that must not land in their timestamp's hour - used for a row that
    arrived out of order, whose hour is already complete: filing it there would replace a whole
    hour with a fragment. Its real timestamp is kept in the bucket's detail; only its bucket is
    the unplaced one.
    """
    forced = unplaced_ids or set()
    buckets: dict[int, dict[str, Any]] = {}
    seen: set[str] = set(known_codes or ())

    for position, row in enumerate(rows, start=1):
        codes = list(dict.fromkeys(str(code) for code in (row.get("reason_codes") or [])))
        ts = str(row.get("ts") or "")
        epoch = parse_ts_epoch(ts)
        row_id = row.get("id")
        if row_id is not None and int(row_id) in forced:
            key = UNPLACED_HOUR
        else:
            key = hour_start(epoch) if epoch is not None else UNPLACED_HOUR

        bucket = buckets.get(key)
        if bucket is None:
            bucket = {
                "hour_epoch": key,
                "cycles": 0,
                "verdicts": {},
                "codes": {},
                "newest_codes": [],
                "sessions": {},
                "sessions_error": None,
                "first_ts": ts,
                "last_ts": ts,
                "last_row_id": 0,
            }
            buckets[key] = bucket

        offset = int(bucket["cycles"])
        bucket["cycles"] = offset + 1
        verdict = str(row.get("verdict") or "UNKNOWN")
        verdicts = bucket["verdicts"]
        verdicts[verdict] = int(verdicts.get(verdict, 0)) + 1

        present = set(codes)
        # Everything that already existed and is missing from this cycle has just cleared.
        for code in seen:
            if code in present:
                continue
            entry = bucket["codes"].setdefault(code, _code_entry())
            entry["last_absent"] = ts
        for code in codes:
            entry = bucket["codes"].setdefault(code, _code_entry())
            if entry["first_offset"] is None:
                entry["first_offset"] = offset
                entry["first"] = ts
            entry["n"] = int(entry["n"]) + 1
            entry["last"] = ts
            seen.add(code)

        bucket["newest_codes"] = codes
        bucket["last_ts"] = ts
        # Journalled rows carry an id; hand-built rows do not, so scan position orders those. Both
        # are monotonic within one call, which is all "the newest cycle" needs.
        bucket["last_row_id"] = max(
            int(bucket["last_row_id"]), int(row_id) if row_id is not None else position
        )

        if key != UNPLACED_HOUR and epoch is not None:
            # Sessions are measured from the cycle's own instant, not the hour's start, so a DST
            # change inside the hour cannot mislabel a cycle.
            try:
                label = session_label(epoch)
            except SessionsUnavailable as exc:
                bucket["sessions_error"] = str(exc)
            else:
                session = bucket["sessions"].setdefault(label, {"cycles": 0, "codes": {}})
                session["cycles"] = int(session["cycles"]) + 1
                for code in codes:
                    counts = session["codes"]
                    counts[code] = int(counts.get(code, 0)) + 1

    return [buckets[key] for key in sorted(buckets)]


def refresh(store: Store, *, now_epoch: int, window_days: int) -> dict[str, Any]:
    """Fold new cycles into hours and prune aged-out hours. Returns what it did, for the journal.

    Only the newest hour and anything after it are touched: older hours are already complete, so
    the work per refresh is one hour of cycles rather than the whole window.
    """
    newest_hour, resume_id = store.gate_rollup_resume()
    raw_rows = store.gate_raw_rows_after(resume_id)

    written = 0
    unplaced = 0
    if raw_rows:
        known: set[str] = set()
        if newest_hour is not None:
            # Codes seen in the hours we are *not* rewriting still exist, so an hour that omits one
            # has cleared it rather than never having seen it.
            known = store.gate_rollup_known_codes(before_hour_epoch=newest_hour)
        late: set[int] = set()
        if newest_hour is not None:
            for row in raw_rows:
                epoch = parse_ts_epoch(str(row.get("ts") or ""))
                if epoch is not None and UNPLACED_HOUR < hour_start(epoch) < newest_hour:
                    late.add(int(row["id"]))
        rolled = roll_hours(raw_rows, known_codes=known, unplaced_ids=late)
        store.upsert_gate_rollup(rolled)
        written = len(rolled)
        unplaced = sum(
            int(hour["cycles"]) for hour in rolled if int(hour["hour_epoch"]) == UNPLACED_HOUR
        )

    cutoff = hour_start(now_epoch - window_days * 86_400)
    pruned = store.prune_gate_rollup(cutoff)
    return {
        "hours_written": written,
        "rows_read": len(raw_rows),
        "pruned_hours": pruned,
        "unplaced_cycles": unplaced,
        "retention_cutoff_hour": cutoff,
    }
