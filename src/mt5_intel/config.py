"""Settings loaded from the project's .env file. Error messages never include credential values."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REQUIRED_CREDENTIALS = ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER")


class ConfigError(RuntimeError):
    """Raised when required settings are missing or invalid."""


@dataclass(frozen=True)
class StopManage:
    """The two rules that move a stop after entry, and the levels they act at.

    Split out of `Settings` because it is the one part of the configuration that has to be
    read by two engines: the live watcher, which sends the moves to the broker, and the
    freqtrade backtester, which has to reproduce them or it is measuring a system that does
    not exist. `load_stop_manage` is the single reader, so a change to one of these numbers
    in the .env moves both at once.
    """

    manage: bool
    breakeven_r: float
    lock_r: float
    trail_r: float
    trail_distance_r: float


def load_stop_manage() -> StopManage:
    """The stop-management settings, without demanding broker credentials.

    The freqtrade engine runs in a separate venv and never logs in, so asking it for
    `load_settings` would fail on missing credentials it has no use for. This reads the same
    five variables `load_settings` reads, so the backtest and the watcher cannot drift.
    """
    return StopManage(
        manage=_flag("AUTO_MANAGE", True),
        breakeven_r=_number("AUTO_BREAKEVEN_R", 1.0),
        lock_r=_number("AUTO_LOCK_R", 0.1),
        trail_r=_number("AUTO_TRAIL_R", 2.0),
        trail_distance_r=_number("AUTO_TRAIL_DISTANCE_R", 1.0),
    )


@dataclass(frozen=True)
class Settings:
    login: int
    password: str
    server: str
    terminal_path: str | None
    utc_offset_hours: float | None
    symbol_root: str
    reference_equity_zar: float
    risk_pct: float
    usd_zar_fallback: float
    history_bars: int
    spread_days: int
    tick_watch_seconds: float
    report_dir: Path
    # Auto-trading. `auto_trade` is the hard master and is off unless the .env says otherwise, so
    # a fresh checkout places no orders even though the code can. `auto_allow_real` is deliberately
    # a *second* switch: the first one arms trading, this one allows it to be a live account.
    #
    # These carry defaults so a Settings built by hand - the diagnostics and the tests do that -
    # is the safe one: off, demo-only, nothing allowed, and no risk budget, which sizes to nothing
    # rather than to something. `load_settings` fills every field from the .env, so the defaults
    # only ever apply to a construction that did not say otherwise.
    #
    # `auto_allow_unvalidated` is the fourth deliberate act, and the only one that relaxes a
    # research rule rather than a technical one: it lets a strategy with no validated edge be
    # traded. It is off unless the .env (or the dashboard's own acknowledgement) says otherwise,
    # and every trade it permits is flagged as unvalidated where it is recorded.
    auto_trade: bool = False
    auto_allow_real: bool = False
    auto_strategies: tuple[str, ...] = ()
    auto_allow_unvalidated: bool = False
    auto_max_positions: int = 2
    auto_magic: int = 20261013
    auto_risk_pct: float = 0.0
    # Position management. Unlike the entries above, these only ever tighten a trade's risk: a
    # stop that gets moved up, a target that a later signal improves, and a close the opposite
    # signal forces. `auto_manage` is on by default for exactly that reason - an open trade the
    # watcher never looks at again is the case that turned the first confluence position into a
    # near-loss.
    auto_manage: bool = True
    auto_breakeven_r: float = 1.0
    auto_lock_r: float = 0.1
    auto_trail_r: float = 2.0
    auto_trail_distance_r: float = 1.0
    auto_tp_handoff: bool = True
    auto_reverse_close: bool = True
    # Extra watched symbols. Empty by default, so a Settings built by hand - the tests -
    # watches exactly what it always watched.
    symbols: tuple[str, ...] = ()


def _get(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _number(name: str, default: float) -> float:
    raw = _get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc


def _flag(name: str, default: bool = False) -> bool:
    """A yes/no setting. Only the words below count as on, so a typo reads as off."""
    raw = _get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def watch_symbols(settings: Settings) -> tuple[str, ...]:
    """The symbols the watch loop builds views for, primary first.

    `symbol_root` remains the primary: it owns the legacy artifact names (`setups.json`,
    `auto.json`) and the auto-trading step. A `SYMBOLS` list adds further symbols, which get
    their own setups artifact (`setups_<SYMBOL>.json`) and their own chart export
    (`mt5intel_<SYMBOL>_<TF>.csv`). The primary is never dropped from this list: reordering it
    changes which symbol the legacy names mean, so the list is read as "primary plus extras"
    rather than as an arbitrary order.
    """
    if not settings.symbols:
        return (settings.symbol_root,)
    return (settings.symbol_root, *(s for s in settings.symbols if s != settings.symbol_root))


def load_settings(env_file: Path | None = None) -> Settings:
    load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)
    missing = [name for name in REQUIRED_CREDENTIALS if _get(name) is None]
    if missing:
        raise ConfigError("Missing values in .env: " + ", ".join(missing))
    login_raw = _get("MT5_LOGIN") or ""
    if not login_raw.isdigit():
        raise ConfigError("MT5_LOGIN must be the numeric account number")
    offset_raw = _get("MT5_UTC_OFFSET_HOURS")
    report_dir = PROJECT_ROOT / (_get("REPORT_DIR") or "reports")
    stop = load_stop_manage()
    return Settings(
        login=int(login_raw),
        password=_get("MT5_PASSWORD") or "",
        server=_get("MT5_SERVER") or "",
        terminal_path=_get("MT5_TERMINAL_PATH"),
        utc_offset_hours=None if offset_raw is None else _number("MT5_UTC_OFFSET_HOURS", 0.0),
        symbol_root=(_get("SYMBOL_ROOT") or "USDJPY").upper(),
        symbols=tuple(
            name.strip().upper()
            for name in (_get("SYMBOLS") or "").split(",")
            if name.strip()
        ),
        reference_equity_zar=_number("REFERENCE_EQUITY_ZAR", 100.0),
        risk_pct=_number("RISK_PCT", 1.0),
        usd_zar_fallback=_number("USD_ZAR_FALLBACK", 18.0),
        history_bars=int(_number("HISTORY_BARS", 300_000)),
        spread_days=int(_number("SPREAD_DAYS", 30)),
        tick_watch_seconds=_number("TICK_WATCH_SECONDS", 30.0),
        report_dir=report_dir,
        auto_trade=_flag("AUTO_TRADE", False),
        auto_allow_real=_flag("AUTO_TRADE_ALLOW_REAL", False),
        auto_strategies=tuple(
            name.strip()
            for name in (_get("AUTO_TRADE_STRATEGIES") or "").split(",")
            if name.strip()
        ),
        auto_allow_unvalidated=_flag("AUTO_TRADE_ALLOW_UNVALIDATED", False),
        auto_max_positions=int(_number("AUTO_TRADE_MAX_POSITIONS", 2)),
        auto_magic=int(_number("AUTO_TRADE_MAGIC", 20261013)),
        auto_risk_pct=_number("AUTO_TRADE_RISK_PCT", 0.0) or _number("RISK_PCT", 1.0),
        auto_manage=stop.manage,
        auto_breakeven_r=stop.breakeven_r,
        auto_lock_r=stop.lock_r,
        auto_trail_r=stop.trail_r,
        auto_trail_distance_r=stop.trail_distance_r,
        auto_tp_handoff=_flag("AUTO_TP_HANDOFF", True),
        auto_reverse_close=_flag("AUTO_REVERSE_CLOSE", True),
    )
