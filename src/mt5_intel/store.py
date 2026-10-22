"""SQLite storage for events, recommendations, decisions and notification dedupe.

This is the journal required by D-028: every cycle appends facts, never overwrites them.
One module owns the store; other modules import these accessors, not the file path.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_kind_ts ON events(kind, ts);

CREATE TABLE IF NOT EXISTS recommendations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  bar_epoch INTEGER NOT NULL,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'watch',
  reason_codes TEXT NOT NULL,
  verdict TEXT NOT NULL,
  payload TEXT NOT NULL,
  UNIQUE(symbol, bar_epoch, kind) ON CONFLICT IGNORE
);
CREATE INDEX IF NOT EXISTS idx_rec_ts ON recommendations(ts);

CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  rec_id INTEGER NOT NULL REFERENCES recommendations(id),
  ts TEXT NOT NULL,
  decision TEXT NOT NULL,
  note TEXT
);

CREATE TABLE IF NOT EXISTS strategies (
  family TEXT PRIMARY KEY,
  timeframe TEXT NOT NULL,
  status TEXT NOT NULL,
  evidence TEXT NOT NULL,
  updated_ts TEXT NOT NULL
);

-- One row per UTC day per account: the first equity the watcher saw that day. A daily-loss
-- percentage needs it, and the adapter places no orders so there is no trade history to derive
-- it from. The account is part of the key because the journal outlives a login: without it, a
-- new account would be measured against the previous account's opening equity.
CREATE TABLE IF NOT EXISTS equity_days (
  day TEXT PRIMARY KEY,
  equity REAL NOT NULL,
  balance REAL NOT NULL,
  ts TEXT NOT NULL,
  -- The account this baseline belongs to. NULL is a row written before the journal named
  -- accounts; it matches only another unknown owner.
  account INTEGER
);

-- Hourly rollup of the watch journal, so the 90-day gate summary reads hours instead of every
-- cycle and retention can drop what has aged out. One row per UTC hour holds the counts the
-- summary needs, including per-code detail, because a summary that only had totals could not
-- answer when a code first appeared or when it last cleared. `hour_epoch` is the UTC hour start;
-- -1 is the bucket for a cycle that cannot be placed in an hour at all (its timestamp did not
-- parse, or it arrived out of order), which the summary reports rather than dropping.
CREATE TABLE IF NOT EXISTS gate_rollup (
  hour_epoch INTEGER NOT NULL,
  kind TEXT NOT NULL,
  cycles INTEGER NOT NULL,
  verdicts TEXT NOT NULL,
  codes TEXT NOT NULL,
  newest_codes TEXT NOT NULL,
  sessions TEXT NOT NULL,
  sessions_error TEXT,
  first_ts TEXT,
  last_ts TEXT,
  last_row_id INTEGER NOT NULL,
  rolled_ts TEXT NOT NULL,
  PRIMARY KEY (kind, hour_epoch)
);

-- One row per order this system placed on its own. The UNIQUE key is the whole point: it is what
-- makes "one position per signal" true across a re-fire, a restart and a crash, because the row is
-- written BEFORE the order is sent (status 'placing') and the insert is ignored if it is already
-- there. A duplicate row would be a duplicate position, so the guarantee lives in the schema rather
-- than in the caller remembering to check.
CREATE TABLE IF NOT EXISTS auto_trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy TEXT NOT NULL,
  families TEXT NOT NULL,
  side TEXT NOT NULL,
  symbol TEXT NOT NULL DEFAULT '',
  signal_epoch INTEGER NOT NULL,
  signal_utc TEXT,
  status TEXT NOT NULL,
  reason TEXT,
  ticket INTEGER,
  entry REAL,
  sl REAL,
  tp REAL,
  volume REAL,
  stop_pips REAL,
  tp_r REAL,
  risk_amount REAL,
  equity REAL,
  risk_pct REAL,
  digits INTEGER,
  retcode INTEGER,
  broker_comment TEXT,
  magic INTEGER,
  expires_epoch INTEGER,
  code_version TEXT NOT NULL,
  open_ts TEXT NOT NULL,
  closed_ts TEXT,
  close_reason TEXT,
  close_ticket INTEGER,
  UNIQUE(symbol, strategy, signal_epoch, side)
);
CREATE INDEX IF NOT EXISTS idx_auto_status ON auto_trades(status);
CREATE INDEX IF NOT EXISTS idx_auto_symbol_status ON auto_trades(symbol, status);
"""

DEDUPE_WINDOW = timedelta(minutes=30)


@dataclass(frozen=True)
class Decision:
    rec_id: int
    decision: str
    note: str | None = None


def _primary_symbol_from_env() -> str:
    """The primary symbol as this build's caller would compute it, for the journal backfill.

    The migration runs at import-time of the store, before a Settings exists to ask, so this
    reads the same rule the config module applies - SYMBOL_ROOT, upper-cased, USDJPY by default
    - rather than importing the config module (which loads .env and could fail this caller for
    the answer a migration only wants as a backfill name).
    """
    return os.environ.get("SYMBOL_ROOT", "USDJPY").strip().upper() or "USDJPY"


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns a journal written by an earlier build is missing.

    `CREATE TABLE IF NOT EXISTS` leaves an existing table alone, so a database created before
    `equity_days` carried an account keeps its old shape and the new column has to be added in
    place. Existing rows keep NULL, which reads as an unknown owner rather than as this account.
    """
    # A fresh database has none of these tables yet: the schema script that follows this
    # migration creates each table in its current shape, so migrations only act on tables the
    # script left alone (IF NOT EXISTS) - those an earlier build wrote in an older shape.
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(equity_days)")}
    # The auto-trading journal gains its symbol column, and with it a rebuilt UNIQUE key: the old
    # one - strategy, bar, side - would go on refusing a second symbol's claim on the same signal,
    # and an ADD COLUMN cannot rewrite a constraint. Every row written before the watcher traded a
    # second symbol was the primary's, so the backfill names it rather than leaving rows that read
    # as "no symbol" - which would be uncounted forever after.
    auto_columns = {row["name"] for row in conn.execute("PRAGMA table_info(auto_trades)")} if "auto_trades" in tables else set()
    # A pre-existing auto_trades predates the symbol column entirely; a fresh database gets its
    # shaped table from _SCHEMA in a moment, so there is nothing to rebuild here.
    if auto_columns and "symbol" not in auto_columns:
        before = conn.execute("SELECT * FROM auto_trades").fetchall()
        conn.execute("ALTER TABLE auto_trades RENAME TO auto_trades_old")
        conn.executescript(
            """
            CREATE TABLE auto_trades (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              strategy TEXT NOT NULL,
              families TEXT NOT NULL,
              side TEXT NOT NULL,
              symbol TEXT NOT NULL DEFAULT '',
              signal_epoch INTEGER NOT NULL,
              signal_utc TEXT,
              status TEXT NOT NULL,
              reason TEXT,
              ticket INTEGER,
              entry REAL,
              sl REAL,
              tp REAL,
              volume REAL,
              stop_pips REAL,
              tp_r REAL,
              risk_amount REAL,
              equity REAL,
              risk_pct REAL,
              digits INTEGER,
              retcode INTEGER,
              broker_comment TEXT,
              magic INTEGER,
              expires_epoch INTEGER,
              code_version TEXT NOT NULL,
              open_ts TEXT NOT NULL,
              closed_ts TEXT,
              close_reason TEXT,
              close_ticket INTEGER,
              UNIQUE(symbol, strategy, signal_epoch, side)
            );
            CREATE INDEX IF NOT EXISTS idx_auto_status ON auto_trades(status);
            CREATE INDEX IF NOT EXISTS idx_auto_symbol_status ON auto_trades(symbol, status);
            """
        )
        primary = _primary_symbol_from_env()
        for row in before:
            conn.execute(
                "INSERT INTO auto_trades SELECT id, strategy, families, side, ?, signal_epoch,"
                " signal_utc, status, reason, ticket, entry, sl, tp, volume, stop_pips, tp_r,"
                " risk_amount, equity, risk_pct, digits, retcode, broker_comment, magic,"
                " expires_epoch, code_version, open_ts, closed_ts, close_reason, close_ticket"
                " FROM auto_trades_old WHERE id = ?",
                (primary, int(row["id"])),
            )
        conn.execute("DROP TABLE auto_trades_old")
    if "equity_days" in tables and "account" not in columns:
        conn.execute("ALTER TABLE equity_days ADD COLUMN account INTEGER")


def _auto_trade_row(row: sqlite3.Row) -> dict[str, Any]:
    """An auto_trades row with its JSON `families` column decoded back into a list."""
    out = dict(row)
    raw = out.get("families")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            out["families"] = [str(item) for item in parsed]
    return out


class Store:
    """Connection-per-method keeps the worker, dashboard and tests independent."""

    def __init__(self, path: Path) -> None:
        self.path = path
        with _connect(path) as conn:
            # Migration first: a database written by an earlier build has tables in their old
            # shape, and _SCHEMA's `CREATE TABLE IF NOT EXISTS` would leave those alone while
            # its new indexes then fail on columns the old tables do not have - which is how a
            # watcher dies on first start after an upgrade. Migrating the existing shape before
            # applying the schema means the schema's no-op creates and index creations always
            # see the new shape.
            _migrate(conn)
            conn.executescript(_SCHEMA)

    def append_event(self, kind: str, payload: dict[str, Any]) -> int:
        with _connect(self.path) as conn:
            cur = conn.execute(
                "INSERT INTO events(ts, kind, payload) VALUES (?, ?, ?)",
                (datetime.now(UTC).isoformat(timespec="seconds"), kind, json.dumps(payload, default=str)),
            )
            return int(cur.lastrowid or 0)

    def add_recommendation(
        self,
        bar_epoch: int,
        symbol: str,
        side: str,
        reason_codes: list[str],
        verdict: str,
        payload: dict[str, Any],
    ) -> int:
        with _connect(self.path) as conn:
            kind = payload.get("kind", "watch")
            key = (symbol, bar_epoch, kind)
            conn.execute(
                "INSERT OR IGNORE INTO recommendations(ts, bar_epoch, symbol, side, kind, reason_codes, verdict, payload)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    bar_epoch,
                    symbol,
                    side,
                    kind,
                    ",".join(reason_codes),
                    verdict,
                    json.dumps(payload, default=str),
                ),
            )
            # lastrowid is the existing row's id only if we SELECT it, so select canonically.
            row = conn.execute(
                "SELECT id FROM recommendations WHERE symbol = ? AND bar_epoch = ? AND kind = ?",
                key,
            ).fetchone()
            return int(row["id"]) if row is not None else 0

    def record_decision(self, rec_id: int, decision: str, note: str | None = None) -> None:
        with _connect(self.path) as conn:
            conn.execute(
                "INSERT INTO decisions(rec_id, ts, decision, note) VALUES (?, ?, ?, ?)",
                (rec_id, datetime.now(UTC).isoformat(timespec="seconds"), decision, note),
            )

    def recent_events(self, kind: str, limit: int = 50) -> list[dict[str, Any]]:
        with _connect(self.path) as conn:
            rows = conn.execute(
                "SELECT id, ts, kind, payload FROM events WHERE kind = ? ORDER BY id DESC LIMIT ?",
                (kind, limit),
            ).fetchall()
        return [
            {"id": row["id"], "ts": row["ts"], "kind": row["kind"], "payload": json.loads(row["payload"])}
            for row in rows
        ]

    def recent_recommendations(self, limit: int = 50) -> list[dict[str, Any]]:
        with _connect(self.path) as conn:
            rows = conn.execute(
                "SELECT id, ts, bar_epoch, symbol, side, reason_codes, verdict, payload"
                " FROM recommendations ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "ts": row["ts"],
                "bar_epoch": row["bar_epoch"],
                "symbol": row["symbol"],
                "side": row["side"],
                "reason_codes": row["reason_codes"].split(",") if row["reason_codes"] else [],
                "verdict": row["verdict"],
                "payload": json.loads(row["payload"]),
            }
            for row in rows
        ]

    def gate_history_rows(self, since_ts: str | None = None) -> list[dict[str, Any]]:
        """Every journaled gate outcome, oldest first - the raw material for a code summary.

        Unlike `recent_recommendations` (newest first, for display) this is chronological
        and windowed, because counting how often a code appears only means something when
        the rows are in order. `since_ts` is an ISO string compared against the row's ts.
        """
        query = "SELECT ts, verdict, reason_codes FROM recommendations WHERE kind = 'watch'"
        params: tuple[Any, ...] = ()
        if since_ts is not None:
            query += " AND ts >= ?"
            params = (since_ts,)
        query += " ORDER BY id"
        with _connect(self.path) as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "ts": row["ts"],
                "verdict": row["verdict"],
                "reason_codes": row["reason_codes"].split(",") if row["reason_codes"] else [],
            }
            for row in rows
        ]

    def equity_baseline(self, day: str, account: int | None = None) -> dict[str, Any] | None:
        """The day's opening equity *for this account* (`YYYY-MM-DD`), or None.

        A baseline another account recorded is not this account's to read: measuring a fresh
        login against the previous account's equity reads as either a windfall or a catastrophic
        loss, and the latter would trip the daily-loss breaker on paper only. A row whose account
        is unknown (NULL, written before the journal named accounts) matches only an unknown
        caller, so the first cycle that can name its account re-baselines the day under it.
        """
        with _connect(self.path) as conn:
            row = conn.execute(
                "SELECT day, equity, balance, ts, account FROM equity_days WHERE day = ?", (day,)
            ).fetchone()
        if row is None or row["account"] != account:
            return None
        return {
            "day": str(row["day"]),
            "equity": float(row["equity"]),
            "balance": float(row["balance"]),
            "ts": str(row["ts"]),
            "account": row["account"],
        }

    def set_equity_baseline(
        self, day: str, equity: float, balance: float, account: int | None = None
    ) -> None:
        """Record a day's opening equity once per account.

        The first sighting of the day wins, so no later cycle can move the baseline. The one
        exception is a different account: it takes the day over, because the alternative is a new
        login inheriting an equity figure that was never its own.
        """
        with _connect(self.path) as conn:
            conn.execute(
                "INSERT INTO equity_days(day, equity, balance, ts, account) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(day) DO UPDATE SET equity = excluded.equity,"
                " balance = excluded.balance, ts = excluded.ts, account = excluded.account"
                " WHERE equity_days.account IS NOT excluded.account",
                (day, equity, balance, datetime.now(UTC).isoformat(timespec="seconds"), account),
            )

    def decisions_for(self, rec_id: int) -> list[dict[str, Any]]:
        with _connect(self.path) as conn:
            rows = conn.execute(
                "SELECT ts, decision, note FROM decisions WHERE rec_id = ? ORDER BY id",
                (rec_id,),
            ).fetchall()
        return [{"ts": row["ts"], "decision": row["decision"], "note": row["note"]} for row in rows]

    def set_strategy_status(
        self, family: str, timeframe: str, status: str, evidence: dict[str, Any]
    ) -> None:
        """Upsert the validation status of one research family (gate reads this)."""
        with _connect(self.path) as conn:
            conn.execute(
                "INSERT INTO strategies(family, timeframe, status, evidence, updated_ts)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(family) DO UPDATE SET timeframe = excluded.timeframe,"
                " status = excluded.status, evidence = excluded.evidence, updated_ts = excluded.updated_ts",
                (
                    family,
                    timeframe,
                    status,
                    json.dumps(evidence, default=str),
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )

    def strategy_statuses(self) -> list[dict[str, Any]]:
        """Every family in the registry with its standing - not only the validated ones.

        The auto-trading panel has to offer a *choice*, and a choice needs the whole list plus what
        each family's last trial measured. Reading it from the registry the gate itself reads is
        what keeps the panel from offering something the gate would then refuse.
        """
        with _connect(self.path) as conn:
            rows = conn.execute(
                "SELECT family, timeframe, status, evidence, updated_ts FROM strategies"
                " ORDER BY family"
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            try:
                evidence = json.loads(row["evidence"])
            except (TypeError, ValueError):
                # Evidence is diagnostic detail, not a gate input: one unreadable row must not hide
                # the rest of the registry from the panel.
                evidence = {}
            out.append(
                {
                    "family": row["family"],
                    "timeframe": row["timeframe"],
                    "status": row["status"],
                    "evidence": evidence,
                    "updated_ts": row["updated_ts"],
                }
            )
        return out

    def validated_strategies(self) -> list[dict[str, Any]]:
        with _connect(self.path) as conn:
            rows = conn.execute(
                "SELECT family, timeframe, status, evidence, updated_ts FROM strategies"
                " WHERE status = 'historically validated'"
            ).fetchall()
        return [
            {
                "family": row["family"],
                "timeframe": row["timeframe"],
                "status": row["status"],
                "evidence": json.loads(row["evidence"]),
                "updated_ts": row["updated_ts"],
            }
            for row in rows
        ]

    # --- auto-trading ----------------------------------------------------------------
    # These four are the whole of the auto-trade journal: reserve, settle, close, read. The
    # reserve/insert pair is what makes the UNIQUE key above an actual guarantee, so nothing else
    # should ever write this table directly.

    def reserve_auto_trade(
        self,
        *,
        strategy: str,
        families: tuple[str, ...],
        side: str,
        signal_epoch: int,
        signal_utc: str | None,
        entry: float,
        sl: float,
        tp: float,
        volume: float,
        stop_pips: float,
        tp_r: float,
        risk_amount: float,
        equity: float,
        risk_pct: float,
        digits: int,
        expires_epoch: int,
        magic: int,
        code_version: str,
        open_ts: str,
        symbol: str = "",
    ) -> int | None:
        """Claim the right to trade one signal. None means an earlier cycle already claimed it.

        Written as 'placing' before the order is sent, so a process that dies between the request
        and its answer leaves a claim behind rather than a signal that can be traded twice. The
        row is settled into 'placed' or 'rejected' a moment later; a 'placing' row read back after
        a restart is reconciled like any other - it either has a live ticket or it never got one.

        The claim names its symbol. The same strategy firing the same bar on a second symbol is a
        different trade: a different spec, a different price, a different risk amount - so the two
        claims cannot spend each other's dedup.
        """
        with _connect(self.path) as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO auto_trades(strategy, families, side, signal_epoch,"
                " signal_utc, status, entry, sl, tp, volume, stop_pips, tp_r, risk_amount,"
                " equity, risk_pct, digits, magic, expires_epoch, code_version, open_ts, symbol)"
                " VALUES (?, ?, ?, ?, ?, 'placing', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    strategy,
                    json.dumps(list(families), default=str),
                    side,
                    int(signal_epoch),
                    signal_utc,
                    float(entry),
                    float(sl),
                    float(tp),
                    float(volume),
                    float(stop_pips),
                    float(tp_r),
                    float(risk_amount),
                    float(equity),
                    float(risk_pct),
                    int(digits),
                    int(magic),
                    int(expires_epoch),
                    code_version,
                    open_ts,
                    symbol,
                ),
            )
            if not cur.rowcount:
                return None
            return int(cur.lastrowid or 0)

    def settle_auto_trade(
        self,
        row_id: int,
        *,
        status: str,
        ticket: int | None,
        retcode: int | None,
        note: str | None = None,
    ) -> None:
        """Record what the terminal did with a reserved request."""
        with _connect(self.path) as conn:
            conn.execute(
                "UPDATE auto_trades SET status = ?, ticket = ?, retcode = ?, broker_comment = ?"
                " WHERE id = ?",
                (status, ticket, retcode, note, int(row_id)),
            )

    def close_auto_trade(
        self, row_id: int, *, reason: str, close_ticket: int | None = None
    ) -> None:
        """Mark an auto trade finished, whether the broker closed it or the watcher did."""
        with _connect(self.path) as conn:
            conn.execute(
                "UPDATE auto_trades SET status = 'closed', close_reason = ?, close_ticket = ?,"
                " closed_ts = ? WHERE id = ?",
                (reason, close_ticket, datetime.now(UTC).isoformat(timespec="seconds"), int(row_id)),
            )

    def auto_trade_taken(
        self, strategy: str, signal_epoch: int, side: str, symbol: str = ""
    ) -> bool:
        """Has this exact signal bar already been claimed, by any cycle and any run of the watcher?

        Every status counts, including a rejected or half-written one: a signal that was refused by
        the broker is not one to try again on the next cycle, and treating it as untaken would
        place the same order every 30 seconds until one got through.

        The claim is per symbol: the same strategy firing the same bar on EURUSD is a different
        trade from its USDJPY twin, on a different spec at a different price, and the two cannot
        spend each other's dedup.
        """
        with _connect(self.path) as conn:
            row = conn.execute(
                "SELECT 1 FROM auto_trades"
                " WHERE symbol = ? AND strategy = ? AND signal_epoch = ? AND side = ?",
                (symbol, strategy, int(signal_epoch), side),
            ).fetchone()
        return row is not None

    def update_auto_trade_stops(
        self, row_id: int, *, sl: float | None = None, tp: float | None = None
    ) -> None:
        """Record what a position's protective stops now are, after the broker accepted the move.

        Only called after a modification the terminal answered OK: the journal must read as what the
        broker holds, not as what was asked for. Whichever argument is left None leaves that column
        alone, since one cycle can move the stop with the target untouched or the other way round.
        """
        sets = [f"{name} = ?" for name, value in (("sl", sl), ("tp", tp)) if value is not None]
        values = [value for value in (sl, tp) if value is not None]
        if not sets:
            return
        with _connect(self.path) as conn:
            conn.execute(
                f"UPDATE auto_trades SET {', '.join(sets)} WHERE id = ?",
                (*values, int(row_id)),
            )

    def auto_trades_open(self, symbol: str = "") -> list[dict[str, Any]]:
        """Auto trades that are, as far as the journal knows, still in the market.

        'placing' is included deliberately: a claim with no answer yet is either a live position
        (the request went through and the process died before settling) or nothing at all, and the
        reconciler is the thing that decides which - counting it here is what stops it being
        double-spent in the meantime. With a symbol named, only that symbol's rows come back, so a
        per-symbol step counts only the positions it supervises.
        """
        with _connect(self.path) as conn:
            if symbol:
                rows = conn.execute(
                    "SELECT * FROM auto_trades"
                    " WHERE status IN ('placing', 'placed') AND symbol = ?"
                    " ORDER BY id",
                    (symbol,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM auto_trades"
                    " WHERE status IN ('placing', 'placed')"
                    " ORDER BY id"
                ).fetchall()
        return [_auto_trade_row(row) for row in rows]

    def recent_rejected_auto_trades(self, limit: int = 5) -> list[dict[str, Any]]:
        """Reservations the terminal refused, newest first.

        The journal is the only place these survive. A refused signal is spent - it is never retried
        - so the cycle that saw the refusal is the last one that could report it, and a panel fed
        only by the current cycle would show an armed switch, no positions and no reason. Reading
        the last few back is what lets the panel say "the broker refused this, and why" for longer
        than thirty seconds.
        """
        with _connect(self.path) as conn:
            rows = conn.execute(
                "SELECT * FROM auto_trades WHERE status = 'rejected' ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [_auto_trade_row(row) for row in rows]

    def recent_auto_trades(self, limit: int = 50) -> list[dict[str, Any]]:
        with _connect(self.path) as conn:
            rows = conn.execute(
                "SELECT * FROM auto_trades ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [_auto_trade_row(row) for row in rows]

    # --- gate rollup -----------------------------------------------------------------
    # The summary is rebuilt every few minutes for a 90-day window. Reading raw cycles for that
    # makes the cost grow with the journal, so cycles are folded into hours once and the summary
    # reads hours. These accessors keep the folding honest: the newest hour is always rebuilt from
    # its own rows rather than merged into, so a re-run cannot double-count it.

    def gate_rollup_resume(self, kind: str = "watch") -> tuple[int | None, int]:
        """(newest rolled hour, id to resume from) - the id being the newest row already folded
        into any *older* hour, so re-reading from it rebuilds the newest hour whole."""
        with _connect(self.path) as conn:
            rows = conn.execute(
                "SELECT hour_epoch, last_row_id FROM gate_rollup WHERE kind = ? AND hour_epoch >= 0"
                " ORDER BY hour_epoch DESC LIMIT 2",
                (kind,),
            ).fetchall()
        if not rows:
            return None, 0
        newest = int(rows[0]["hour_epoch"])
        resume = int(rows[1]["last_row_id"]) if len(rows) > 1 else 0
        return newest, resume

    def gate_raw_rows_after(self, row_id: int, kind: str = "watch") -> list[dict[str, Any]]:
        """Raw watch cycles inserted after `row_id`, oldest first."""
        with _connect(self.path) as conn:
            rows = conn.execute(
                "SELECT id, ts, reason_codes, verdict FROM recommendations"
                " WHERE kind = ? AND id > ? ORDER BY id",
                (kind, row_id),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "ts": str(row["ts"]),
                "verdict": str(row["verdict"]),
                "reason_codes": row["reason_codes"].split(",") if row["reason_codes"] else [],
                "kind": kind,
            }
            for row in rows
        ]

    def upsert_gate_rollup(self, hours: list[dict[str, Any]], kind: str = "watch") -> None:
        """Write whole hour buckets, replacing any earlier version of the same hour."""
        if not hours:
            return
        now_ts = datetime.now(UTC).isoformat(timespec="seconds")
        with _connect(self.path) as conn:
            conn.executemany(
                "INSERT INTO gate_rollup(hour_epoch, kind, cycles, verdicts, codes, newest_codes,"
                " sessions, sessions_error, first_ts, last_ts, last_row_id, rolled_ts)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(kind, hour_epoch) DO UPDATE SET cycles = excluded.cycles,"
                " verdicts = excluded.verdicts, codes = excluded.codes,"
                " newest_codes = excluded.newest_codes, sessions = excluded.sessions,"
                " sessions_error = excluded.sessions_error, first_ts = excluded.first_ts,"
                " last_ts = excluded.last_ts, last_row_id = excluded.last_row_id,"
                " rolled_ts = excluded.rolled_ts",
                [
                    (
                        int(hour["hour_epoch"]),
                        kind,
                        int(hour["cycles"]),
                        json.dumps(hour["verdicts"], default=str),
                        json.dumps(hour["codes"], default=str),
                        json.dumps(hour["newest_codes"], default=str),
                        json.dumps(hour["sessions"], default=str),
                        hour.get("sessions_error"),
                        hour.get("first_ts"),
                        hour.get("last_ts"),
                        int(hour.get("last_row_id") or 0),
                        now_ts,
                    )
                    for hour in hours
                ],
            )

    def gate_rollup_rows(
        self, kind: str = "watch", before_hour_epoch: int | None = None
    ) -> list[dict[str, Any]]:
        """Rolled hours, oldest first - the only read the 90-day summary needs.

        `before_hour_epoch` narrows it to hours older than a point, which is how the folder finds
        the codes that already existed before the hour it is about to rewrite.
        """
        query = (
            "SELECT hour_epoch, cycles, verdicts, codes, newest_codes, sessions, sessions_error,"
            " first_ts, last_ts, last_row_id, rolled_ts FROM gate_rollup WHERE kind = ?"
        )
        params: tuple[Any, ...] = (kind,)
        if before_hour_epoch is not None:
            query += " AND hour_epoch < ?"
            params = (kind, before_hour_epoch)
        query += " ORDER BY hour_epoch"
        with _connect(self.path) as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "hour_epoch": int(row["hour_epoch"]),
                "cycles": int(row["cycles"]),
                "verdicts": json.loads(row["verdicts"]),
                "codes": json.loads(row["codes"]),
                "newest_codes": json.loads(row["newest_codes"]),
                "sessions": json.loads(row["sessions"]),
                "sessions_error": row["sessions_error"],
                "first_ts": row["first_ts"],
                "last_ts": row["last_ts"],
                "last_row_id": int(row["last_row_id"]),
                "rolled_ts": str(row["rolled_ts"]),
            }
            for row in rows
        ]

    def gate_rollup_known_codes(self, before_hour_epoch: int, kind: str = "watch") -> set[str]:
        """Codes that appeared in any rolled hour older than a point.

        The folder needs this to know that an hour which omits a code has *cleared* it rather than
        never having seen it. Extracting the keys in SQL keeps the answer to one row per code
        instead of one row per hour, which matters once the window holds two thousand hours.
        """
        with _connect(self.path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT key FROM gate_rollup, json_each(gate_rollup.codes)"
                " WHERE kind = ? AND hour_epoch < ?",
                (kind, before_hour_epoch),
            ).fetchall()
        return {str(row[0]) for row in rows}

    def prune_gate_rollup(self, before_hour_epoch: int, kind: str = "watch") -> int:
        """Drop rolled hours that have aged out of the summary window.

        The unplaced bucket is kept: it is the record of cycles that could not be placed in time
        at all, which is a bug signal worth keeping rather than something to age out.
        """
        with _connect(self.path) as conn:
            cur = conn.execute(
                "DELETE FROM gate_rollup WHERE kind = ? AND hour_epoch >= 0 AND hour_epoch < ?",
                (kind, before_hour_epoch),
            )
            return int(cur.rowcount or 0)

    def should_notify(self, signature: str, now: datetime | None = None) -> bool:
        """Dedupe identical alert signatures within the cooldown window."""
        now = now or datetime.now(UTC)
        cutoff = (now - DEDUPE_WINDOW).isoformat(timespec="seconds")
        with _connect(self.path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM events WHERE kind = 'notify' AND payload LIKE ?"
                " AND ts > ?",
                (f'%{signature}%', cutoff),
            ).fetchone()
            if row["n"] > 0:
                return False
            conn.execute(
                "INSERT INTO events(ts, kind, payload) VALUES (?, 'notify', ?)",
                (now.isoformat(timespec="seconds"), json.dumps({"signature": signature})),
            )
            return True
