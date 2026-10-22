"""Milestone 1: read-only MT5 diagnostic for USD/JPY on an HFM Cent account. Places no orders."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np

from .config import Settings
from .datachecks import (
    estimate_server_offset_s,
    find_gaps,
    is_market_open_utc,
    offset_is_plausible,
    spread_by_hour,
)
from .mt5_adapter import ACCOUNT_TRADE_MODES, SYMBOL_TRADE_MODES, ReadOnlyMT5
from .riskfit import (
    SymbolSpec,
    max_stop_at_min_lot,
    pip_size,
    pip_value_per_lot,
    reference_equity_usc,
    risk_budget_usc,
    risk_table,
    usc_to_zar,
)

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
NOT_VERIFIED = "NOT_VERIFIED"
INFO = "INFO"
HISTORY_TIMEFRAMES: dict[str, int] = {"M1": 60, "M5": 300, "H1": 3600}
SPREAD_TIMEFRAME = "M5"
BARS_PER_DAY_M5 = 288
SPEC_RETRIES = 3
SPEC_RETRY_S = 1.0
# Live probe (HFM, 2026-10-10): 80,000 bars served on M1/M5/H1; 100,000 rejected with
# 'Terminal: Invalid params' even though terminal maxbars reports 100,000.
SAFE_REQUEST_BARS = 80_000


@dataclass
class Check:
    area: str
    name: str
    status: str
    detail: str


@dataclass
class Report:
    generated_utc: str
    checks: list[Check] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def add(self, area: str, name: str, status: str, detail: str) -> None:
        self.checks.append(Check(area, name, status, detail))

    @property
    def overall(self) -> str:
        statuses = {check.status for check in self.checks}
        for status in (FAIL, NOT_VERIFIED, WARN):
            if status in statuses:
                return status
        return PASS


def _mask(login: int) -> str:
    return f"***{str(login)[-3:]}"


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat(timespec="minutes")


# Errors a check can raise against a live or fake terminal. Anything else propagates and stops the run.
CHECK_ERRORS = (ConnectionError, OSError, RuntimeError, ValueError, KeyError, AttributeError,
                IndexError, TypeError)


def _guard(report: Report, area: str, name: str, action: Callable[[], object]) -> None:
    try:
        action()
    except CHECK_ERRORS as exc:  # fail closed: an unverifiable check is never reported as passing
        report.add(area, name, NOT_VERIFIED, f"{type(exc).__name__}: {exc}")


def _select_symbol_name(candidates: list[str]) -> str | None:
    if not candidates:
        return None
    return next((name for name in candidates if name.endswith(("c", "C"))), candidates[0])


def _check_account(report: Report, mt5: ReadOnlyMT5) -> None:
    info = mt5.account_info()
    if info is None:
        report.add("account", "connection", NOT_VERIFIED, f"account_info unavailable: {mt5.last_error()}")
        return
    mode = ACCOUNT_TRADE_MODES.get(int(info.trade_mode), f"UNKNOWN({info.trade_mode})")
    report.data["account"] = {
        "login_masked": _mask(int(info.login)),
        "server": info.server,
        "company": info.company,
        "currency": info.currency,
        "leverage": info.leverage,
        "mode": mode,
        "trade_allowed": bool(info.trade_allowed),
        "margin_call_pct": info.margin_so_call,
        "stop_out_pct": info.margin_so_so,
        "balance": info.balance,
        "equity": info.equity,
    }
    report.add(
        "account",
        "currency",
        PASS if info.currency == "USC" else FAIL,
        f"{info.currency}; expected USC for an HFM Cent account (D-034)",
    )
    report.add(
        "account",
        "mode",
        PASS if mode == "DEMO" else FAIL,
        f"{mode}; milestone 1 runs on a demo account only",
    )
    report.add(
        "account",
        "trade_allowed",
        WARN if info.trade_allowed else PASS,
        "trading flag set; use the investor (read-only) password (D-020)"
        if info.trade_allowed
        else "trading flag off (investor/read-only session)",
    )


def _check_symbol(
    report: Report, mt5: ReadOnlyMT5, root: str, sleep: Callable[[float], None]
) -> SymbolSpec | None:
    pattern = re.compile(rf"^{re.escape(root)}[A-Za-z]{{0,3}}$", re.IGNORECASE)
    candidates = [s.name for s in mt5.symbols_get(f"*{root}*") if pattern.match(s.name)]
    name = _select_symbol_name(candidates)
    if name is None:
        report.add("symbol", "identify", FAIL, f"no symbol matching {root}* found")
        return None
    report.add("symbol", "identify", PASS if name.endswith(("c", "C")) else WARN,
               f"chosen {name} from {candidates}; Cent symbols end in 'c' (D-034)")
    info = mt5.symbol_info(name)
    if info is None:
        report.add("symbol", "specs", NOT_VERIFIED, f"symbol_info unavailable for {name}")
        return None
    if not info.visible:
        mt5.symbol_select(name, True)
        info = mt5.symbol_info(name)
    # Right after connecting, the terminal may still be syncing specs (tick value reads as 0).
    for _ in range(SPEC_RETRIES):
        if float(info.trade_tick_value) > 0 and float(info.trade_tick_size) > 0:
            break
        sleep(SPEC_RETRY_S)
        info = mt5.symbol_info(name)
    spec = SymbolSpec(
        name=name,
        digits=int(info.digits),
        point=float(info.point),
        tick_size=float(info.trade_tick_size),
        tick_value=float(info.trade_tick_value),
        volume_min=float(info.volume_min),
        volume_step=float(info.volume_step),
        volume_max=float(info.volume_max),
    )
    trade_mode = SYMBOL_TRADE_MODES.get(int(info.trade_mode), f"UNKNOWN({info.trade_mode})")
    report.data["symbol"] = {
        "name": name,
        "candidates": candidates,
        "digits": spec.digits,
        "point": spec.point,
        "pip_size": pip_size(spec),
        "tick_size": spec.tick_size,
        "tick_value_usc": spec.tick_value,
        "contract_size": float(info.trade_contract_size),
        "volume_min": spec.volume_min,
        "volume_step": spec.volume_step,
        "volume_max": spec.volume_max,
        "trade_mode": trade_mode,
        "currency_profit": info.currency_profit,
        "swap_long": float(info.swap_long),
        "swap_short": float(info.swap_short),
        "spread_points_now": int(info.spread),
    }
    report.add("symbol", "trade_mode", PASS if trade_mode == "FULL" else WARN, trade_mode)
    contract = float(info.trade_contract_size)
    report.add(
        "symbol",
        "contract_size",
        PASS if contract == 1000 else WARN,
        f"{contract:g} units per lot (Cent expects 1000)",
    )
    report.add(
        "symbol",
        "volume_spec",
        PASS if spec.volume_min == 0.01 and spec.volume_step == 0.01 else WARN,
        f"min {spec.volume_min:g}, step {spec.volume_step:g}, max {spec.volume_max:g}",
    )
    return spec


def _usd_zar_rate(report: Report, mt5: ReadOnlyMT5, fallback: float) -> float:
    pattern = re.compile(r"^USDZAR[A-Za-z]{0,3}$", re.IGNORECASE)
    names = [s.name for s in mt5.symbols_get("*USDZAR*") if pattern.match(s.name)]
    if names:
        mt5.symbol_select(names[0], True)
        tick = mt5.symbol_info_tick(names[0])
        if tick is not None and tick.bid > 0 and tick.ask > 0:
            rate = (float(tick.bid) + float(tick.ask)) / 2
            report.data["usd_zar"] = {"rate": rate, "source": f"MT5 symbol {names[0]}"}
            report.add("rate", "usd_zar", PASS, f"{rate:.4f} from {names[0]}")
            return rate
    report.data["usd_zar"] = {"rate": fallback, "source": "configured fallback (assumption)"}
    report.add(
        "rate",
        "usd_zar",
        WARN,
        f"no live USD/ZAR from MT5; using configured fallback {fallback} (assumption)",
    )
    return fallback


def _check_risk(report: Report, spec: SymbolSpec, settings: Settings, usd_zar: float) -> None:
    budget = risk_budget_usc(settings.reference_equity_zar, usd_zar, settings.risk_pct)
    equity_usc = reference_equity_usc(settings.reference_equity_zar, usd_zar)
    pip_value = pip_value_per_lot(spec)
    max_stop = max_stop_at_min_lot(spec, budget)
    rows = risk_table(spec, budget)
    report.data["risk"] = {
        "reference_equity_zar": settings.reference_equity_zar,
        "reference_equity_usc": equity_usc,
        "risk_pct": settings.risk_pct,
        "risk_budget_usc": budget,
        "risk_budget_zar": usc_to_zar(budget, usd_zar),
        "pip_value_per_lot_usc": pip_value,
        "pip_value_per_min_lot_usc": pip_value * spec.volume_min,
        "pip_value_per_min_lot_zar": usc_to_zar(pip_value * spec.volume_min, usd_zar),
        "max_stop_pips_at_min_lot": max_stop,
        "table": [
            {
                "stop_pips": row.stop_pips,
                "risk_per_min_lot_zar": usc_to_zar(row.risk_per_min_lot, usd_zar),
                "max_volume": row.max_volume,
                "actual_risk_zar": usc_to_zar(row.actual_risk, usd_zar),
                "fits": row.fits,
            }
            for row in rows
        ],
    }
    report.add(
        "risk",
        "min_lot_stop_ceiling",
        PASS if max_stop >= 20 else WARN,
        f"largest stop at min lot within {settings.risk_pct:g}% budget: {max_stop:.1f} pips",
    )


def _check_liveness(
    report: Report, mt5: ReadOnlyMT5, name: str, watch_s: float, sleep: Callable[[float], None]
) -> bool:
    first = mt5.symbol_info_tick(name)
    start = time.monotonic()
    while time.monotonic() - start < watch_s:
        sleep(1.0)
        current = mt5.symbol_info_tick(name)
        if current is not None and first is not None and current.time_msc != first.time_msc:
            report.add("tick", "liveness", PASS, f"tick advanced within {watch_s:g} s")
            return True
    report.add(
        "tick",
        "liveness",
        WARN,
        f"no tick update in {watch_s:g} s while market open (quiet or stale feed)",
    )
    return False


def _resolve_offset(
    report: Report,
    mt5: ReadOnlyMT5,
    name: str,
    settings: Settings,
    now_epoch: int,
    live: bool,
) -> int | None:
    if settings.utc_offset_hours is not None:
        offset = int(settings.utc_offset_hours * 3600)
        source = "configured MT5_UTC_OFFSET_HOURS"
    elif live:
        tick = mt5.symbol_info_tick(name)
        offset = estimate_server_offset_s(int(tick.time), now_epoch)
        source = "estimated from live tick"
    else:
        report.data["utc_offset"] = {"seconds": None, "source": "unknown"}
        report.add(
            "time",
            "utc_offset",
            NOT_VERIFIED,
            "market closed and no MT5_UTC_OFFSET_HOURS set; UTC conversion skipped",
        )
        return None
    report.data["utc_offset"] = {"seconds": offset, "source": source}
    plausible = offset_is_plausible(offset)
    report.add(
        "time",
        "utc_offset",
        PASS if plausible else FAIL,
        f"server = UTC{offset / 3600:+.0f}h ({source}); HFM is expected at +2h or +3h",
    )
    return offset


def _capped_count(report: Report, mt5: ReadOnlyMT5, requested: int) -> int:
    """The terminal rejects requests above its 'max bars in chart' limit, so cap and say so."""
    maxbars = mt5.terminal_max_bars()
    limit = SAFE_REQUEST_BARS if maxbars is None else min(maxbars, SAFE_REQUEST_BARS)
    if requested <= limit:
        return requested
    report.add("history", "bar_cap", INFO,
               f"requested {requested} bars; per-request limit is {limit} (terminal max {maxbars}); capped")
    return limit


def _check_history(
    report: Report, mt5: ReadOnlyMT5, name: str, requested: int, offset: int | None
) -> None:
    count = _capped_count(report, mt5, requested)
    history: dict[str, Any] = {}
    for timeframe, interval in HISTORY_TIMEFRAMES.items():
        rates = mt5.copy_rates_from_pos(name, timeframe, 0, count)
        if rates.size == 0:
            report.add("history", timeframe, NOT_VERIFIED,
                       f"no bars returned (terminal: {mt5.last_error()})")
            continue
        times = np.asarray(rates["time"], dtype=np.int64)
        base = times - offset if offset is not None else times
        gaps = find_gaps(base, interval)
        unexpected = [g for g in gaps if not g.weekend_closure]
        longest = max((g.missing_s for g in unexpected), default=0)
        history[timeframe] = {
            "bars": int(times.size),
            "first_utc": _iso(int(base[0])),
            "last_utc": _iso(int(base[-1])),
            "span_days": round((int(base[-1]) - int(base[0])) / 86_400, 1),
            "gaps_weekend": len(gaps) - len(unexpected),
            "gaps_unexpected": len(unexpected),
            "longest_unexpected_gap_h": round(longest / 3600, 2),
            "utc_classified": offset is not None,
        }
        report.add(
            "history",
            timeframe,
            INFO,
            f"{times.size} bars, {history[timeframe]['span_days']} days, "
            f"{len(unexpected)} unexpected gaps (longest {longest / 3600:.1f} h)"
            + ("" if offset is not None else "; server clock, UTC unknown, weekend split unreliable"),
        )
    report.data["history"] = history


def _check_spread(report: Report, mt5: ReadOnlyMT5, spec: SymbolSpec, settings: Settings,
                  offset: int | None) -> None:
    count = _capped_count(report, mt5, settings.spread_days * BARS_PER_DAY_M5)
    rates = mt5.copy_rates_from_pos(spec.name, SPREAD_TIMEFRAME, 0, count)
    if rates.size == 0:
        report.add("cost", "spread_by_hour", NOT_VERIFIED,
                   f"no M5 bars returned (terminal: {mt5.last_error()})")
        return
    times = np.asarray(rates["time"], dtype=np.int64)
    base = times - offset if offset is not None else times
    spread_pips = np.asarray(rates["spread"], dtype=np.float64) * spec.point / pip_size(spec)
    stats = spread_by_hour(base, spread_pips)
    report.data["spread_by_hour"] = {
        "utc_classified": offset is not None,
        "bar_timeframe": SPREAD_TIMEFRAME,
        "note": "bar spread is a snapshot per bar, not the full tick cost path",
        "hours": [
            {"hour_utc": s.hour, "hour_sast": (s.hour + 2) % 24, "samples": s.samples,
             "median_pips": s.median, "p90_pips": s.p90}
            for s in stats
        ],
    }
    report.add("cost", "spread_by_hour", INFO, f"{times.size} M5 bars over {settings.spread_days} days")


def run_diagnostic(
    settings: Settings,
    backend: Any,
    now: datetime | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Report:
    now = now or datetime.now(UTC)
    now_epoch = int(now.timestamp())
    report = Report(generated_utc=now.isoformat(timespec="seconds"))
    mt5 = ReadOnlyMT5(backend)
    mt5.connect(
        login=settings.login,
        password=settings.password,
        server=settings.server,
        terminal_path=settings.terminal_path,
    )
    try:
        _check_account(report, mt5)
        spec = _check_symbol(report, mt5, settings.symbol_root, sleep)
        usd_zar = _usd_zar_rate(report, mt5, settings.usd_zar_fallback)
        if spec is None:
            return report
        _guard(report, "risk", "table",
               lambda: _check_risk(report, spec, settings, usd_zar))
        live = is_market_open_utc(now_epoch)
        report.data["market_open_utc"] = live
        if live:
            _guard(report, "tick", "liveness",
                   lambda: _check_liveness(report, mt5, spec.name, settings.tick_watch_seconds, sleep))
        else:
            report.add("tick", "liveness", INFO, "market closed (UTC weekend); staleness not evaluated")
        offset: int | None = None
        try:
            offset = _resolve_offset(report, mt5, spec.name, settings, now_epoch, live)
        except CHECK_ERRORS as exc:  # fail closed, same as _guard
            report.add("time", "utc_offset", NOT_VERIFIED, f"{type(exc).__name__}: {exc}")
        _guard(report, "history", "depth",
               lambda: _check_history(report, mt5, spec.name, settings.history_bars, offset))
        _guard(report, "cost", "spread_by_hour",
               lambda: _check_spread(report, mt5, spec, settings, offset))
        return report
    finally:
        mt5.shutdown()

