"""Continuous watch loop. Every cycle:

1. Connect to MT5 (read-only adapter) and read account/symbol state.
2. Compute data freshness, spread, session, swing/structure events from closed M1 bars.
3. Run the full gate (all checks, all reason codes, default-deny).
4. Journal every outcome — including SKIPs — as events; recommendations go in their own table.
5. Write an atomic live-status JSON for the dashboard, with a heartbeat the consumer treats as
   stale when old (per the backend skill).
6. Notify on state changes; deduped; notification failures never crash the cycle.
7. Disconnect, reconnect with backoff; verify recovery before normal operation resumes.

No MT5 order functions exist on the adapter, and the read-only source scan enforces that.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import execution, gate_rollup, gatehistory, market_export, notify, trades
from .config import ConfigError, Settings, load_settings, watch_symbols
from .datachecks import (
    FAST_MOVE_ATR_BARS,
    FAST_MOVE_ATR_MULT,
    FastMove,
    estimate_server_offset_s,
    fast_move,
    find_gaps,
    is_market_open_utc,
    offset_is_plausible,
    spread_by_hour,
)
from .gate import GateInputs, evaluate
from .mt5_adapter import ReadOnlyMT5
from .news import NewsUnavailable, fetch_and_cache, next_event, within_news_window
from .research import export_mt5
from .research import setups as live_setups
from .research.backtest import load_bars
from .research.replay import DEFAULT_MAX_HOLD
from .store import Store

POLL_SECONDS = 30.0
IDLE_POLL_SECONDS = 300.0  # market closed: nothing to read, wake up occasionally
BACKOFF_START = 5.0
BACKOFF_MAX = 300.0
HEARTBEAT_TTL_OPEN_S = 180
HEARTBEAT_TTL_IDLE_S = 900
PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATUS_FILE = PROJECT_ROOT / "dashboard" / "live-status.json"
STATUS_MAX_AGE_S = 180  # dashboard shows STALE past this heartbeat age
# Reason-code history for the Health page. Summarising means a pass over the journal, so
# it is refreshed on a timer rather than every cycle; the artifact carries its own
# generated_at, so the dashboard can show how old the summary is.
GATE_HISTORY_FILE = PROJECT_ROOT / "dashboard" / "gate-history.json"
GATE_HISTORY_REFRESH_S = 300
# The live per-strategy view: which card each rule is in *now*, and how it is doing. Rewritten on
# a timer rather than every cycle, because scanning 12 families is a second of work, while a new
# H1 bar - the only thing that can change the answer - arrives once an hour.
SETUPS_FILE = PROJECT_ROOT / "dashboard" / "setups.json"
SETUPS_REFRESH_S = 300
# The same span the replay grades over (about 2.3 years of H1), so a live card's cost in R is
# measured against the same spread history the replay pages use.
SETUPS_BARS = 20_000
# The same trades, written where an MT5 chart can read them (MQL5\Files), so the rules can be
# looked at on the chart rather than only read as numbers. Same bars, same decision, same refresh
# as the panel above, so the two cannot show different trades.
CHART_EXPORT_S = 300
# The trade journal: what the account actually did, read from the terminal's own positions and
# deal history. A minute of its own, not the artifact's five: a live row is a floating result, and
# the question the page answers - "am I up or down on this one?" - is worth answering promptly.
TRADES_FILE = PROJECT_ROOT / "dashboard" / "trades.json"
TRADES_REFRESH_S = 60
# Auto-trading. The switch is a file the dashboard writes and this loop reads every cycle, so
# turning trading off takes effect on the next cycle rather than on the next restart; the artifact
# is what the dashboard shows back, including when the switch is off and why.
AUTO_SWITCH_FILE = PROJECT_ROOT / "dashboard" / "auto-switch.json"
AUTO_FILE = PROJECT_ROOT / "dashboard" / "auto.json"
# The dashboard's strategy selection, read on every cycle for the same reason the switch is: it
# changes which strategies may trade without a restart, and turning one off takes effect on the next
# cycle rather than at the next launch.
AUTO_SELECTION_FILE = PROJECT_ROOT / "dashboard" / "auto-strategies.json"
# The dashboard's per-symbol position caps, read every cycle like the two files above: a cap
# change is a risk decision made in the moment, so it applies on the next cycle, not the
# next restart. The filename lives with its reader in execution, so neither copy can drift.
AUTO_CAPS_FILE = PROJECT_ROOT / "dashboard" / execution.CAPS_FILENAME
REPORT_DIR = PROJECT_ROOT / "reports"
NEWS_CACHE_PATH = PROJECT_ROOT / "data" / "news_cache.json"


# Waits before giving up on the rename, in seconds. A Windows sharing violation is the case
# this exists for: the dashboard reading live-status.json, a virus scanner or a file-sync client
# holding it open at the instant of the replace. Each entry is a retry after the previous one
# failed, so the total wait is under a second - longer than any of those holds it.
RENAME_RETRY_SLEEP_S = (0.05, 0.15, 0.4)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write a JSON artifact atomically, and do not lose the write to a momentary lock.

    The replace is the point of this function: a reader sees either the old file or the new
    one, never half of either. On Windows it is also the step that fails with a sharing
    violation (WinError 5/32, both PermissionError) when another process has the destination
    open without sharing deletion - a transient condition, so it is retried before anything is
    given up on.

    If the rename still cannot happen, the file is written in place rather than left stale. A
    reader that catches that write mid-flight sees a truncated file, which the dashboard
    already tolerates (`readSetups`/`readLiveStatus` return "not written yet" on a parse
    failure), and a view that is briefly absent beats a view that is silently days old.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, default=str, indent=2)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    for delay in (0.0, *RENAME_RETRY_SLEEP_S):
        if delay:
            time.sleep(delay)
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            continue  # held open by someone else right now; wait and try the rename again
    try:
        tmp.unlink()
    except OSError:
        pass
    path.write_text(text, encoding="utf-8")


def _write_status(store: Store, path: Path, payload: dict[str, Any]) -> None:
    """Write the dashboard's status artifact, and let it fail without taking the loop with it.

    Every other artifact in this loop is reporting, and a reporting write must never stop the
    watcher - that is the rule the setups and gate-history refreshers already follow. This one
    was the exception, and it cost a night: on 2026-10-12 22:14 UTC a sharing violation raised
    inside the cycle's status write, the cycle handler wrote the same file and raised again from
    inside its own handler, and the process exited. The dashboard went on showing a Monday-night
    view, with the cards' ages frozen at the numbers written then, while the market moved.
    """
    try:
        _atomic_write_json(path, payload)
    except OSError as exc:
        store.append_event("status_error", {"error": type(exc).__name__, "message": str(exc)})


def _epoch_to_iso(epoch_s: int) -> str:
    return datetime.fromtimestamp(epoch_s, tz=UTC).isoformat(timespec="seconds")


class FixedOffset:
    """A fake monotonic clock for tests."""

    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def _read_state(mt5: ReadOnlyMT5, name: str) -> dict[str, Any]:
    tick = mt5.symbol_info_tick(name)
    rates = mt5.copy_rates_from_pos(name, "M1", 0, 601)
    account = mt5.account_info()
    info = mt5.symbol_info(name)     # Read-only exposure: the gate's position cap counts what OPEN the engine holds - its
     # magic-tagged trades - not the account total. The holder's manual trades are their own book:
     # counting them had every card answer "position limit" while the machine still had room, and
     # the cap means "where the engine stands", not "where the account stands". The account total
     # still travels in the recommendation payload below so nothing about the account is hidden.
    # An unreadable read is None, not an exception: D-013 says an unverified position state
    # default-denies, and a failed exposure read must not stop the whole watch loop.
    positions: tuple[Any, ...] | None
    try:
        positions = mt5.positions_get()
    except Exception:  # noqa: BLE001 - default-deny beats a dead watcher
        positions = None
    return {
        "tick": tick,
        "rates": rates,
        "account": account,
        "symbol_info": info,
        "positions": positions,
        "server_error": mt5.last_error(),
    }


def _broker_offset_s(settings: Settings, state: dict[str, Any], now_epoch: int) -> int | None:
    """Server-clock offset from UTC, resolved the way the diagnostic resolves it.

    Configured first, otherwise estimated from the live tick. The broker's clock is what stamps
    every bar and what bounds its own day, so an offset that cannot be resolved is None: inputs
    that depend on it then read as unverified rather than silently assumed UTC.
    """
    if settings.utc_offset_hours is not None:
        return int(settings.utc_offset_hours * 3600)
    tick = state.get("tick")
    if tick is None:
        return None
    # A terminal that has just logged in can still serve the last tick it held before the login,
    # and after a weekend that tick is days old: now-and-four-days-ago is not an offset, it is a
    # stale feed, and every other offset resolver here (diagnostic, backtest, exporter) refuses an
    # implausible estimate for exactly that reason. Trusting one would key the broker day - and
    # with it the daily-loss baseline - to the wrong date entirely.
    offset = estimate_server_offset_s(int(tick.time), now_epoch)
    return offset if offset_is_plausible(offset) else None


def _daily_loss_pct(
    store: Store, account: Any, now_epoch: int, offset_s: int | None
) -> float | None:
    """Percent below the broker day's opening equity, from the journal's own baseline.

    The adapter places no orders and keeps no trade history, so the day's opening equity is the
    first equity this watcher saw that day *for this account*, recorded once in `equity_days`.
    The account matters: the journal outlives a login, so a baseline another account recorded is
    never read here - one account's opening balance is not the yardstick for another's equity. A
    cycle whose equity - or whose server offset, and so the day itself - cannot be read stays
    unverified (None) rather than reporting zero loss, because a fabricated zero would pass the
    daily-loss check silently.
    """
    equity = getattr(account, "equity", None) if account is not None else None
    if equity is None or offset_s is None:
        return None
    # The broker's day: its clock is what its own daily loss limit is measured on.
    day = datetime.fromtimestamp(now_epoch + offset_s, tz=UTC).date().isoformat()
    # An account that cannot be named is an unknown owner, not a wildcard: it can only ever match
    # another unknown-owner baseline, and a named account takes the day over from it.
    login = getattr(account, "login", None)
    owner = None if login is None else int(login)
    baseline = store.equity_baseline(day, owner)
    if baseline is None:
        balance = getattr(account, "balance", None)
        store.set_equity_baseline(
            day, float(equity), float(equity if balance is None else balance), owner
        )
        return 0.0
    start = float(baseline["equity"])
    if start <= 0:
        return None
    return max(0.0, (start - float(equity)) / start * 100.0)


def _fast_move_detail(move: FastMove, pip_size_factor: float | None) -> dict[str, Any]:
    """The breaker's reading in pips when the pip size is known, so the journal explains it."""

    def to_pips(value: float | None) -> float | None:
        if value is None or not pip_size_factor:
            return None
        return round(value / pip_size_factor, 2)

    return {
        "measured": move.measured,
        "tripped": move.tripped,
        "ratio": round(move.ratio, 2) if move.ratio is not None else None,
        "atr_pips": to_pips(move.atr),
        "last_range_pips": to_pips(move.last_range),
        "bars": move.bars,
        "mult": FAST_MOVE_ATR_MULT,
    }


# The live cycle evaluates H1 bars; a strategy validated on another timeframe
# (e.g. carry_trend on D1) is not evidence for cards generated here (GAP-9).
WATCH_TIMEFRAME = "H1"
# One bar of that timeframe, in seconds - what an auto-traded card's hold limit is counted in.
WATCH_TIMEFRAME_S = 3600


def _validated_strategy_id(store: Store) -> str | None:
    """First family validated on the watcher's own timeframe, per the registry."""
    for row in store.validated_strategies():
        if row.get("timeframe") == WATCH_TIMEFRAME:
            return str(row["family"])
    return None


def _journal_and_decide(
    store: Store,
    settings: Settings,
    state: dict[str, Any],
    now_epoch: int,
    symbol: str | None = None,
    notify: bool = True,
) -> dict[str, Any]:
    """Compute per-cycle state, run the gate, journal the outcome. Returns cycle summary.

    `symbol` is which market this cycle read: the primary by default (every legacy caller and
    the tests), and one of the extra watched symbols when the loop asks for one by name. Only
    the primary raises notifications: the alert dedup is by message signature, not by symbol,
    so a second symbol repeating "connection lost" would consume or be consumed by another's
    alert window rather than both landing.
    """
    symbol = symbol or settings.symbol_root
    rates = state["rates"]
    account = state["account"]

    # Bar epochs carry the BROKER's clock (HFM runs UTC+2/+3), not UTC. Subtracting the offset is
    # what makes an age meaningful: taken raw the difference is hours negative, so DATA_STALE
    # could never fire and a stale feed would pass the freshness check. No offset - no reading.
    offset_s = _broker_offset_s(settings, state, now_epoch)
    bar_epoch = int(rates["time"][-1]) - offset_s if rates.size and offset_s is not None else None
    bar_age = now_epoch - bar_epoch if bar_epoch is not None else None
    # Pip size comes from the symbol's actual digits, not a hardcoded convention.
    pip_size_factor: float | None = None
    info = state.get("symbol_info")
    if info is not None and float(info.point) > 0:
        pip_size_factor = float(info.point) * 10 if int(info.digits) in (3, 5) else float(info.point)
    spread_pips: float | None = None
    if state["tick"] is not None and pip_size_factor:
        spread_pips = (state["tick"].ask - state["tick"].bid) / pip_size_factor
    connected = state["account"] is not None

    events: list[dict[str, Any]] = []
    # Exposure and the day's loss come from the terminal (read-only). Unreadable stays None so
    # the gate reports it as unverified rather than passing a check nobody measured.
    positions = state.get("positions")
    open_positions = len(positions) if positions is not None else None
    # The auto engine's own budget counts what the engine holds, not what the account does: a
    # manual trade the account holder took in parallel is theirs, and the machine's cap measured
    # against the whole account made every entry answer "position limit" while he was trading.
    # Both the recommendation gate above and the auto step consume this count - one cap, one rule.
    auto_positions = (
        sum(
            1
            for position in positions
            if int(getattr(position, "magic", 0) or 0) == settings.auto_magic
        )
        if positions is not None
        else None
    )
    symbol_positions = (
        sum(
            1
            for position in positions
            if str(getattr(position, "symbol", "")) == symbol
        )
        if positions is not None
        else None
    )
    daily_loss_pct = _daily_loss_pct(store, account, now_epoch, offset_s)

    # The circuit breaker is measured, not assumed: the newest CLOSED bar against the average
    # true range of the bars before it. The last bar from copy_rates_from_pos is still forming.
    closed = rates[:-1] if rates.size > 1 else rates[:0]
    move = fast_move(
        closed["high"],
        closed["low"],
        closed["close"],
        bars=FAST_MOVE_ATR_BARS,
        mult=FAST_MOVE_ATR_MULT,
    )

    # News filter (D-032): verifiable or default-deny. USD/JPY High-impact, ±30 min.
    news_verifiable = True
    news_excluded = True
    news_detail: dict[str, Any] = {"state": "unavailable"}
    try:
        news_events, _ = fetch_and_cache(NEWS_CACHE_PATH)
        news_excluded = within_news_window(news_events, now_epoch)
        upcoming = next_event(news_events, now_epoch)
        news_detail = {
            "state": "ok",
            "excluded": news_excluded,
            "high_events_week": len(news_events),
            "next_event_utc": upcoming.epoch_utc if upcoming else None,
            "next_event_title": upcoming.title if upcoming else None,
        }
    except NewsUnavailable as exc:
        news_verifiable = False
        news_detail = {"state": "unavailable", "error": str(exc)}

    gate = evaluate(
        GateInputs(
            symbol=symbol,
            bar_age_s=bar_age,
            spread_pips=spread_pips,
            market_open=is_market_open_utc(now_epoch) if connected else None,
            news_verifiable=news_verifiable,
            news_excluded=news_excluded,
            daily_loss_pct=daily_loss_pct,
            # The strict cap: the engine's own trades, never the account total (read above).
            open_positions=auto_positions,
            circuit_breaker_tripped=move.tripped,
            validated_strategy=_validated_strategy_id(store),
            # Sizing needs a candidate trade's stop, and nothing produces one yet; card
            # generation brings the stop distance that would make this measurable.
            risk_fits=None,
        )
    )

    rec_id = store.add_recommendation(
        bar_epoch=bar_epoch or 0,
        symbol=symbol,
        side="unknown",
        reason_codes=gate.reason_codes,
        verdict=gate.verdict,
        payload={
            "spread_pips": spread_pips,
            "bar_age_s": bar_age,
            "market_open": is_market_open_utc(now_epoch) if connected else None,
            "validated_strategy": _validated_strategy_id(store),
            "daily_loss_pct": daily_loss_pct,
            "positions": {"total": open_positions, "auto": auto_positions, "symbol": symbol_positions},
            "fast_move": _fast_move_detail(move, pip_size_factor),
            "news": news_detail,
        },
    )

    # Market-computed events are journaled per closed bar, not per cycle. Gap classification and
    # the hour-of-day spread both read a wall clock, so this is journaled from UTC bars only -
    # never from server-time bars labelled as UTC.
    if rates.size and offset_s is not None:
        times = rates["time"].astype("int64") - offset_s
        gaps = find_gaps(times, 60)
        hour = spread_by_hour(times % 86400, rates["spread"].astype("float64") * 0.1)
        events.append(
            {
                "kind": "market_scan",
                "payload": {
                    "symbol": symbol,
                    "bar_epoch": bar_epoch,
                    "gaps_recent": len(gaps),
                    "bar_count": int(times.size),
                    "spread_hour_median_midday_utc": hour[12].median,
                    "fast_move": _fast_move_detail(move, pip_size_factor),
                    "code_version": "watch-v1",
                    "validated_strategy": _validated_strategy_id(store) is not None,
                },
            }
        )

    for event in events:
        store.append_event(event["kind"], event["payload"])

    if notify:
        _notify_state_changes(store, connected, gate, bar_age, spread_pips)

    return {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "connected": connected,
        "symbol": symbol,
        "bar_epoch": bar_epoch,
        "bar_age_s": bar_age,
        "spread_pips": spread_pips,
        "gate_verdict": gate.verdict,
        "gate_reasons": gate.reason_codes,
        "rec_id": rec_id,
        "news": news_detail,
        # The same readings the cycle's own gate was given, kept together under one key so the
        # per-card gate in the auto step is handed exactly the market the cycle saw: a spread
        # measured for the cycle and a spread measured again for a card could disagree, and the
        # card would then be judged on a market nobody else saw.
        "market": {
            "bar_age_s": bar_age,
            "spread_pips": spread_pips,
            "market_open": is_market_open_utc(now_epoch) if connected else None,
            "daily_loss_pct": daily_loss_pct,
            "open_positions": open_positions,
            # Readings for the auto engine's own gate: it budgets against the machine's trades,
            # while the account total above stays in the recommendation layer for you to read.
            "auto_positions": auto_positions,
            "circuit_breaker_tripped": move.tripped,
            "news_verifiable": news_verifiable,
            "news_excluded": news_excluded,
            "offset_s": offset_s,
        },
        "heartbeat": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def _refresh_gate_history(store: Store, now_epoch: int) -> None:
    """Rewrite the gate reason-code summary when it is missing or older than its window.

    Cycles are folded into hourly buckets first (`gate_rollup`) and the summary is built from
    those buckets, so this refresh costs one hour of cycles plus a bounded read rather than a
    scan of the whole window; aged-out hours are dropped as it goes. A reporting artifact must
    never be able to stop the watch loop: any failure is journaled and the cycle carries on.
    """
    try:
        if GATE_HISTORY_FILE.exists():
            age = now_epoch - int(GATE_HISTORY_FILE.stat().st_mtime)
            if age < GATE_HISTORY_REFRESH_S:
                return
        now = datetime.fromtimestamp(now_epoch, tz=UTC)
        rolled = gate_rollup.refresh(store, now_epoch=now_epoch, window_days=gatehistory.WINDOW_DAYS)
        hours = store.gate_rollup_rows()
        summary = gatehistory.summarize_rollup(hours, now=now)
        # What the refresh cost, so the panel can show the summary is reading hours, not cycles.
        summary["rollup"] = {**rolled, "hours_kept": len(hours)}
        _atomic_write_json(GATE_HISTORY_FILE, summary)
    except Exception as exc:  # noqa: BLE001 - a stale panel beats a stopped watcher
        store.append_event(
            "gate_history_error", {"error": type(exc).__name__, "message": str(exc)}
        )


def _setups_extra_path(symbol: str) -> Path:
    """The per-symbol live panel artifact, next to the primary's legacy `setups.json`.

    The primary keeps its old name - every existing reader, and the dashboard's default view,
    read that long before there was a second symbol - and writes its per-symbol copy beside it
    (in the same folder as `SETUPS_FILE`, so a test that relocates the artifact relocates the
    mirrors too), and the dashboard's symbol picker lists every watched market the same way.
    """
    return SETUPS_FILE.parent / f"setups_{symbol}.json"


# The informational multi-timeframe views. The trading registry and the auto step live on H1 by
# name (WATCH_TIMEFRAME), and these artifacts are written only where nothing that trades reads:
# they exist so the panel can show what the same rules are saying on the faster frames, which is
# observation, never a second gate. Their cards carry no research grade on their frame, and the
# dashboard says so wherever they appear.
DISPLAY_TIMEFRAMES: tuple[str, ...] = ("M30", "M15")


def _display_tf_path(symbol: str, timeframe: str) -> Path:
    """The per-symbol, per-timeframe informational view's artifact.

    Named past the setups picks' own convention on purpose (setups_<SYMBOL>.json carries no
    timeframe): an artifact's name must say what frame it is, because a reader that cannot see
    the frame will read an M15 card under H1's authority - and that is exactly the mistake the
    research gate's per-frame rule exists to prevent.
    """
    return SETUPS_FILE.parent / f"setups_{symbol}_{timeframe}.json"


def _refresh_extra_timeframes(
    mt5: ReadOnlyMT5, settings: Settings, store: Store, now_epoch: int
) -> None:
    """The same card view, on the faster frames, as information only.

    Every watched symbol gets an M30 and an M15 copy of the setups view - its own bars, its own
    broker offset, its own artifact - gated by the same refresh window the H1 view uses, every
    failure journaled per symbol and frame so one dead frame cannot cost another its view.

    Nothing here writes a name the auto step or the registry reads (`setups.json`,
    `setups_<SYMBOL>.json`, the `auto*` artifacts), so a faster frame's card cannot inherit
    anything that trades: the reading is display-side by construction, not by an extra check
    that could later be forgotten.
    """
    for symbol in watch_symbols(settings):
        for timeframe in DISPLAY_TIMEFRAMES:
            artifact = _display_tf_path(symbol, timeframe)
            try:
                due = not (
                    artifact.exists()
                    and now_epoch - int(artifact.stat().st_mtime) < SETUPS_REFRESH_S
                )
                chart_due = _chart_export_due(mt5, now_epoch, symbol, timeframe)
                if not due and not chart_due:
                    continue
                offset_s = _broker_offset_s(settings, _read_state(mt5, symbol), now_epoch)
                bars = load_bars(mt5, symbol, timeframe, SETUPS_BARS, utc_offset_s=offset_s)
                if bars is None:
                    continue
                if due:
                    payload = live_setups.build(
                        bars,
                        timeframe,
                        symbol=symbol,
                        offset_s=offset_s or 0,
                        now_epoch=now_epoch,
                    )
                    _atomic_write_json(artifact, payload)
                if chart_due:
                    # The chart's own copy of the frame's reading, files named as
                    # `mt5intel_<SYMBOL>_<TF>.csv` - which is how a chart switched to M30 or
                    # M15 finds its own file instead of falling back to H1's and drawing its
                    # arrows (or none, when no H1 file exists for this symbol).
                    export_mt5.export(mt5, bars, symbol, timeframe, offset_s=offset_s)
            except Exception as exc:  # noqa: BLE001 - one frame's fault is not another's silence
                store.append_event(
                    "setups_error",
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "error": type(exc).__name__,
                        "message": str(exc),
                    },
                )


def _refresh_setups(
    mt5: ReadOnlyMT5, settings: Settings, state: dict[str, Any], store: Store, now_epoch: int
) -> None:
    """Rewrite the primary symbol's live per-strategy artifact when due.

    This is what turns 'no card on the replay page' into 'these rules are in these trades right
    now': the trend families only speak at the start of a move, so a page built solely from
    graded history looks silent in the middle of a trend. Unreadable history leaves the last
    artifact in place, and any failure is journaled: a reporting artifact must never stop the
    watch loop.
    """
    try:
        panel_due = not (
            SETUPS_FILE.exists() and now_epoch - int(SETUPS_FILE.stat().st_mtime) < SETUPS_REFRESH_S
        )
        chart_due = _chart_export_due(mt5, now_epoch, settings.symbol_root)
        if not panel_due and not chart_due:
            return
        # Resolved before the bars are loaded, not after: the bars carry the broker's clock, and
        # the session rules in the scalp module refuse to run without it.
        offset_s = _broker_offset_s(settings, state, now_epoch)
        bars = load_bars(
            mt5, settings.symbol_root, WATCH_TIMEFRAME, SETUPS_BARS, utc_offset_s=offset_s
        )
        if bars is None:
            return
        if panel_due:
            payload = live_setups.build(
                bars,
                WATCH_TIMEFRAME,
                symbol=settings.symbol_root,
                offset_s=offset_s or 0,
                now_epoch=now_epoch,
            )
            _atomic_write_json(SETUPS_FILE, payload)
            # The same card set under the symbol's own name, so the picker and every ?symbol view
            # reads one convention. The legacy file stays the default view.
            _atomic_write_json(_setups_extra_path(settings.symbol_root), payload)
        if chart_due:
            # The same bars and the same clock, so the chart cannot disagree with the panel.
            export_mt5.export(
                mt5, bars, settings.symbol_root, WATCH_TIMEFRAME, offset_s=offset_s
            )
    except Exception as exc:  # noqa: BLE001 - a stale panel beats a stopped watcher
        store.append_event("setups_error", {"error": type(exc).__name__, "message": str(exc)})


def _refresh_extra_symbols(
    mt5: ReadOnlyMT5, settings: Settings, store: Store, now_epoch: int
) -> None:
    """The same panel and chart export, for every watched symbol after the primary.

    Each symbol stands on its own: its bars, its own artifact `setups_<SYMBOL>.json` and its own
    chart files `mt5intel_<SYMBOL>_<TF>.csv`, all gated by the same refresh windows the primary
    uses, every failure journaled per symbol so one dead market cannot cost another its view.
    """
    for symbol in watch_symbols(settings)[1:]:
        try:
            artifact = _setups_extra_path(symbol)
            panel_due = not (
                artifact.exists()
                and now_epoch - int(artifact.stat().st_mtime) < SETUPS_REFRESH_S
            )
            chart_due = _chart_export_due(mt5, now_epoch, symbol)
            if not panel_due and not chart_due:
                continue
            offset_s = _broker_offset_s(settings, _read_state(mt5, symbol), now_epoch)
            bars = load_bars(
                mt5, symbol, WATCH_TIMEFRAME, SETUPS_BARS, utc_offset_s=offset_s
            )
            if bars is None:
                continue
            if panel_due:
                payload = live_setups.build(
                    bars,
                    WATCH_TIMEFRAME,
                    symbol=symbol,
                    offset_s=offset_s or 0,
                    now_epoch=now_epoch,
                )
                _atomic_write_json(artifact, payload)
            if chart_due:
                export_mt5.export(mt5, bars, symbol, WATCH_TIMEFRAME, offset_s=offset_s)
        except Exception as exc:  # noqa: BLE001 - one symbol's fault is not another's silence
            store.append_event(
                "setups_error", {"symbol": symbol, "error": type(exc).__name__, "message": str(exc)}
            )


def _export_market_artifacts(mt5: ReadOnlyMT5, symbols: tuple[str, ...], store: Store, summary: dict[str, Any]) -> None:
    """Refresh the terminal's bar/tick artifacts on the cycle's read-only connection.

    A failure is recorded as an event, not raised: the terminal degrades to its own
    staleness chip and should never take the watcher's journal-and-decide cycle down with it.
    """
    try:
        written = market_export.run_once(mt5, symbols, market_export.DEFAULT_BARS)
        summary["market_export"] = {"written": written}
    except Exception as exc:  # noqa: BLE001 - one chart frame's fault is not the cycle's fault
        store.append_event(
            "market_export_error",
            {"error": type(exc).__name__, "message": str(exc)},
        )


def _chart_export_due(
    mt5: ReadOnlyMT5, now_epoch: int, symbol: str = "", timeframe: str = WATCH_TIMEFRAME
) -> bool:
    """Is the chart's copy of the trades missing or past its window?

    Answered without loading history, so a fresh panel does not force a scan just to decide. A
    terminal that reports no data path (not installed the usual way) simply has no chart export.
    The frame is part of the question because the files are named per timeframe: an M30 chart
    reads `mt5intel_<SYMBOL>_M30.csv`, and only that file's age decides whether M30 is stale.
    """
    try:
        target = export_mt5.chart_path(mt5, timeframe, None, symbol)
    except Exception:  # noqa: BLE001 - an unavailable chart export is not a cycle fault
        return False
    if target is None:
        return False
    if not target.exists():
        return True
    return now_epoch - int(target.stat().st_mtime) >= CHART_EXPORT_S


def _symbol_digits(
    mt5: ReadOnlyMT5, symbols: set[str], watched: str, state: dict[str, Any]
) -> dict[str, int]:
    """Digits per symbol, so a price is quoted the way its own symbol quotes it.

    The watched symbol is already read for the cycle; anything else the account happens to hold is
    looked up once here. A spec that cannot be read just falls back to the caller's default, which
    is the same tolerance every other reader in this loop has for a terminal that will not answer.
    """
    digits: dict[str, int] = {}
    info = state.get("symbol_info")
    if info is not None and int(getattr(info, "digits", 0) or 0) > 0:
        digits[watched] = int(info.digits)
    for symbol in sorted(symbols):
        if not symbol or symbol in digits:
            continue
        try:
            spec = mt5.symbol_info(symbol)
        except Exception:  # noqa: BLE001 - an unreadable spec is not a cycle fault
            spec = None
        if spec is not None and int(getattr(spec, "digits", 0) or 0) > 0:
            digits[symbol] = int(spec.digits)
    return digits


def _refresh_trades(
    mt5: ReadOnlyMT5, settings: Settings, state: dict[str, Any], store: Store, now_epoch: int
) -> None:
    """Rewrite the trade journal artifact when it is missing or past its window.

    The artifact is rebuilt from the terminal rather than accumulated here, because MT5's deal
    history already is the durable record of what the account did. The one thing a rebuild cannot
    re-derive is which strategies had a live card when a trade was taken, so an open trade carries
    its context forward from the previous artifact (see `trades.build`). As with every other
    reporting artifact in this loop, a failure is journaled and the cycle carries on.
    """
    try:
        if TRADES_FILE.exists() and now_epoch - int(TRADES_FILE.stat().st_mtime) < TRADES_REFRESH_S:
            return
        offset_s = _broker_offset_s(settings, state, now_epoch)
        positions = mt5.positions_get()
        start, end = trades.fetch_window(now_epoch)
        deals = mt5.history_deals_get(start, end)
        symbols = {str(getattr(position, "symbol", "") or "") for position in positions}
        account = state.get("account")
        payload = trades.build(
            positions=positions,
            deals=deals,
            # The previous artifact is what lets a trade that is still open keep the strategies
            # that were live when it was first seen, and what lets a closed one keep its label
            # context after the position has gone from the terminal.
            previous=trades.load_artifact(TRADES_FILE),
            setups=trades.load_artifact(SETUPS_FILE),
            watched_symbol=settings.symbol_root,
            digits_by_symbol=_symbol_digits(mt5, symbols, settings.symbol_root, state),
            offset_s=offset_s,
            account=int(getattr(account, "login", 0) or 0) or None,
            currency=getattr(account, "currency", None),
            now_epoch=now_epoch,
        )
        _atomic_write_json(TRADES_FILE, payload)
    except Exception as exc:  # noqa: BLE001 - a stale journal beats a stopped watcher
        store.append_event("trades_error", {"error": type(exc).__name__, "message": str(exc)})


def _auto_step(
    backend: Any,
    settings: Settings,
    store: Store,
    summary: dict[str, Any],
    now_epoch: int,
) -> None:
    """Turn this cycle's live cards into orders, for every watched symbol in turn.

    Reporting artifacts in this loop tolerate failure, and this one has to as well even though it
    is not one: a refusal from the broker, a missing setups file or an unreadable account must
    leave the watch loop running and the reason journaled, because a dead watcher cannot close
    anything. Everything that decides whether to trade lives in `execution`, which is where the
    order surface is pinned by the source scan; this function is the wiring only.

    The switch, the allow-list and the position cap are shared - one arming decision, one risk
    budget across the account - and each symbol's step trades its own market: its own card view,
    its own spec and sizing, its own dedup and its own rows in the journal. The symbol's own
    market readings come from its per-symbol status map; a symbol with no reading there yet is
    skipped this cycle and named in the journal, the same way a card whose view is stale is.

    The gate the engine is judged against reads the positions *the engine owns* (the auto magic),
    not everything the account holds: the account total also carries the holder's own manual
    trades, and counting those had the machine answer "position limit" while the holder was
    trading entirely separate exposure. When the cycle read nothing (None), the engine keeps the
    account total so an unreadable count still refuses Rather than pass.
    """
    registry = [
        dict(row) for row in store.strategy_statuses() if row.get("timeframe") == WATCH_TIMEFRAME
    ]
    validated = {
        str(row["family"]) for row in registry if row.get("status") == "historically validated"
    }
    for symbol in watch_symbols(settings):
        try:
            self_view = summary.get("symbols", {}).get(symbol) if isinstance(summary.get("symbols"), dict) else None
            root_market: dict[str, Any] = summary.get("market") or {}
            market: dict[str, Any] = (
                dict(root_market)
                if symbol == settings.symbol_root
                else {
                    "bar_age_s": (self_view or {}).get("bar_age_s"),
                    "spread_pips": (self_view or {}).get("spread_pips"),
                    # The FX session clock is time-based and shared by these majors; the account
                    # readings (daily loss, breaker) are the account's, not one market's.
                    "market_open": root_market.get("market_open"),
                    "daily_loss_pct": root_market.get("daily_loss_pct"),
                    "open_positions": root_market.get("open_positions"),
                    # The same count the primary's cycle measured beside its account total, so the
                    # engine's cap means the same thing here as there: what the machine holds,
                    # not what the account holder does (the swap below consumes it).
                    "auto_positions": _borrowed(self_view, root_market, "auto_positions"),
                    "circuit_breaker_tripped": root_market.get("circuit_breaker_tripped"),
                    # News IS per market - the per-symbol cycle verifies its own calendar - but a
                    # view that predates its own news reading borrows the primary's verdict rather
                    # than defaulting: a missing key reads as unverified (default-deny), which is
                    # what silently refused every extra-symbol card while the primary traded on.
                    "news_verifiable": _borrowed(self_view, root_market, "news_verifiable"),
                    "news_excluded": _borrowed(self_view, root_market, "news_excluded"),
                    "offset_s": _borrowed(self_view, root_market, "offset_s"),
                }
            )
            # Swap the account total for the engine's own exposure when the cycle measured both.
            auto_positions = market.get("auto_positions")
            if auto_positions is not None and market.get("open_positions") is not None:
                market["open_positions"] = int(auto_positions)
            if symbol != settings.symbol_root and (self_view is None or not self_view.get("available")):
                store.append_event(
                    "auto_skipped",
                    {"symbol": symbol, "reason": "no per-symbol market reading yet this cycle"},
                )
                continue
            setups = trades.load_artifact(
                SETUPS_FILE if symbol == settings.symbol_root else _setups_extra_path(symbol)
            )
            max_hold = 0
            if isinstance(setups, dict):
                value = setups.get("max_hold_bars")
                max_hold = int(value) if isinstance(value, int) else 0
            if max_hold <= 0:
                max_hold = DEFAULT_MAX_HOLD
            offset_s = market.get("offset_s")
            outcome = execution.run(
                trader=execution.MT5Trader(backend),
                store=store,
                settings=settings,
                setups=setups,
                market=market,
                switch_path=AUTO_SWITCH_FILE,
                selection_path=AUTO_SELECTION_FILE,
                caps_file=AUTO_CAPS_FILE,
                now_epoch=now_epoch,
                offset_s=offset_s if isinstance(offset_s, int) else None,
                timeframe_s=WATCH_TIMEFRAME_S,
                max_hold_bars=max_hold,
                validated=validated,
                registry=registry,
                symbol=symbol,
            )
            artifact = execution.artifact(
                outcome, settings, market=market, now_epoch=now_epoch, symbol=symbol
            )
            if symbol == settings.symbol_root:
                _atomic_write_json(AUTO_FILE, artifact)
            else:
                # One view per symbol, next to the primary's, so the panel can show what each
                # market did without one overwriting the other.
                _atomic_write_json(AUTO_FILE.parent / f"auto_{symbol}.json", artifact)
        except Exception as exc:  # noqa: BLE001 - a stopped watcher cannot close anything
            store.append_event(
                "auto_error", {"symbol": symbol, "error": type(exc).__name__, "message": str(exc)}
            )


def _borrowed(
    view: dict[str, Any] | None, root: dict[str, Any], key: str
) -> Any:
    """The per-symbol reading when the symbol has one, otherwise the primary's.

    A view gathered before its market carried a reading (older summaries, or a caller that
    supplies only the fresh bar age) must not read as a *measured* absence: an unreadable news
    verdict and a missing one look the same in `None`, and one is a refusal while the other is
    just an older form of the same summary.
    """
    value = (view or {}).get(key)
    return root.get(key) if value is None else value


def _notify_state_changes(
    store: Store, connected: bool, gate: Any, bar_age: int | None, spread: float | None
) -> None:
    """Only important notifications: connection loss and stale data.

    Per-cycle SKIPs are intentionally NOT notified — 'no trade this bar' is normal operation,
    journaled for later analysis but never worth a toast.
    """
    if not connected:
        notify_alert(store, "disconnect", "MT5 disconnected", "Connection lost; recommendations paused.")
    elif bar_age is not None and bar_age > 180:
        notify_alert(store, "stale", "Data stale", f"No fresh bar for {bar_age} s; recommendations paused.")
    # gate.verdict == SKIP is journaled quietly; a CARD verdict will alert when research ships.


def notify_alert(store: Store, kind: str, title: str, message: str) -> None:
    """Notify wrapper that never lets a broken channel crash the cycle."""
    try:
        notify.alert(store, kind, title, message)
    except Exception:  # noqa: BLE001 — a broken notification path is journaled, never fatal
        store.append_event("notify_error", {"kind": kind, "message": message})


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Config error: {exc}")
        return 2
    try:
        import MetaTrader5 as backend
    except ImportError:
        print("MetaTrader5 package not installed (Windows only).")
        return 2
    store = Store(PROJECT_ROOT / "data" / "mt5.db")
    mt5 = ReadOnlyMT5(backend)
    backoff = BACKOFF_START
    last_market_state: str | None = None
    watched = watch_symbols(settings)
    print(f"Watching {', '.join(watched)}; journal at {store.path}")
    while True:
        now_epoch = int(datetime.now(UTC).timestamp())
        market_open = is_market_open_utc(now_epoch)
        state_str = "open" if market_open else "closed"

        # Market just opened: worth one alert; closed again: one quiet note.
        if last_market_state is not None and last_market_state != state_str:
            if state_str == "open":
                notify_alert(store, "market_open", "Market open", f"Session started; watching for setups on {', '.join(watched)}.")
            else:
                notify_alert(store, "market_closed", "Market closed", "Weekend/idle; watcher polls slowly until reopen.")
        last_market_state = state_str

        # Closed market: skip the gate entirely, write an idle heartbeat, sleep long.
        if not market_open:
            _write_status(
                store,
                STATUS_FILE,
                {
                    "ts": datetime.now(UTC).isoformat(timespec="seconds"),
                    "watcher_alive": True,
                    "mode": "idle",
                    "market_state": "closed",
                    "heartbeat_ttl_s": HEARTBEAT_TTL_IDLE_S,
                    "symbols": {symbol: {"available": False} for symbol in watched[1:]},
                },
            )
            # The journal is unchanged while the market is closed, but the artifact may be
            # missing on a fresh checkout, so it is still worth writing one.
            _refresh_gate_history(store, now_epoch)
            time.sleep(IDLE_POLL_SECONDS)
            continue

        try:
            mt5.connect(
                login=settings.login,
                password=settings.password,
                server=settings.server,
                terminal_path=settings.terminal_path,
            )
            state = _read_state(mt5, settings.symbol_root)
            summary = _journal_and_decide(store, settings, state, now_epoch)
            summary["watcher_alive"] = True
            summary["mode"] = "watching"
            summary["market_state"] = "open"
            summary["heartbeat_ttl_s"] = HEARTBEAT_TTL_OPEN_S
            _write_status(store, STATUS_FILE, summary)
            _refresh_gate_history(store, now_epoch)
            _refresh_setups(mt5, settings, state, store, now_epoch)
            _refresh_trades(mt5, settings, state, store, now_epoch)
            _refresh_extra_symbols(mt5, settings, store, now_epoch)
            # The faster frames' card views ride the same cycle. They are reporting artifacts
            # like every other write here: gated, journaled, and read by the dashboard's own
            # timeframe tabs - never by the auto step, which reads only the H1 artifacts below.
            _refresh_extra_timeframes(mt5, settings, store, now_epoch)
            # The terminal's bar/tick artifacts ride the same read-only connection: refresh
            # them every cycle too, so the web terminal's candles and Market Watch stay
            # live without a second process polling the terminal.
            _export_market_artifacts(mt5, watched, store, summary)
            # Each extra symbol gets its own gated cycle - its own gate readings, its own
            # recommendation rows - gathered into the status's `symbols` map so the dashboard's
            # per-symbol views read one heartbeat, and gathered BEFORE the step below, which reads
            # these as its per-symbol market readings: views attached after the step are what made
            # the extra symbols sit out every cycle of auto trading ("no market reading yet").
            symbol_views: dict[str, Any] = {}
            for extra in watched[1:]:
                try:
                    row = _journal_and_decide(
                        store,
                        settings,
                        _read_state(mt5, extra),
                        now_epoch,
                        symbol=extra,
                        notify=False,
                    )
                    symbol_views[extra] = {
                        "available": True,
                        "ts": row["ts"],
                        "connected": row["connected"],
                        "bar_epoch": row["bar_epoch"],
                        "bar_age_s": row["bar_age_s"],
                        "spread_pips": row["spread_pips"],
                        "gate_verdict": row["gate_verdict"],
                        "gate_reasons": row["gate_reasons"],
                        "rec_id": row["rec_id"],
                        # The readings only the symbol's own cycle measured - the news verdict is
                        # per market, and so is the broker offset that makes its bar age mean
                        # anything. Without these the auto step borrowed the primary's news
                        # verdict, and every extra-symbol card met NEWS_UNVERIFIED.
                        "news_verifiable": (row.get("market") or {}).get("news_verifiable"),
                        "news_excluded": (row.get("market") or {}).get("news_excluded"),
                        "offset_s": (row.get("market") or {}).get("offset_s"),
                        "auto_positions": (row.get("market") or {}).get("auto_positions"),
                    }
                except Exception as exc:  # noqa: BLE001 - one symbol's fault is not another's silence
                    store.append_event(
                        "extra_symbol_error",
                        {"symbol": extra, "error": type(exc).__name__, "message": str(exc)},
                    )
                    symbol_views[extra] = {"available": False}
            summary["symbols"] = symbol_views
            # After the setups artifact is refreshed, so a card is acted on the cycle it appears
            # and the chart, the panel and the order all read the same card.
            _auto_step(backend, settings, store, summary, now_epoch)
            _write_status(store, STATUS_FILE, summary)
            backoff = BACKOFF_START
        except Exception as exc:  # noqa: BLE001 — any cycle fault must degrade to an error event, not stop the loop
            store.append_event("cycle_error", {"error": type(exc).__name__, "message": str(exc)})
            summary = {
                "ts": datetime.now(UTC).isoformat(timespec="seconds"),
                "watcher_alive": False,
                "mode": "error",
                "heartbeat_ttl_s": HEARTBEAT_TTL_IDLE_S,
                "error": type(exc).__name__,
            }
            _write_status(store, STATUS_FILE, summary)
            time.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)
            continue
        finally:
            try:
                mt5.shutdown()
            except Exception:  # noqa: BLE001,S110 — shutdown failure must not block the cycle
                pass
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
