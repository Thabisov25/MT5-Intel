"""Replay one strategy family over recent history and export bars + graded trades.

Powers the strategy page's replay chart (`/strategies/[family]`): the same cards the
recommendation gate would have issued on old bars, each graded against what happened
next, so the page can draw *when a trade could have been taken and how it ended* -
a translucent box from entry to the barrier that resolved it.

Every number here comes from the research core's own pinned trio, never re-derived:

* `one_at_a_time` decides WHICH signals became trades (D-035 flat-only) - the same
  call the live view, the research tables and the MT5 chart export make;
* `walk_trade` decides WHERE each trade ended (SL-first on a double touch, D-012);
* `closed_r` computes its net R (costs included) - the one place R arithmetic lives.

So a box on this chart is the same trade, at the same prices, with the same result the
Live tab and the research reports show for the same family.

Bars are exported on the UTC clock (the exporter resolves the broker offset the same
way `freqtrade_export.py` does) so the chart axis and the trade windows share one
clock with no client-side correction - and the forming bar is dropped: every rule
here is closed-bar-only, and grading a half-built candle would flicker.

Run (project venv, MT5 terminal must be open):

    .venv/Scripts/python.exe tools/family_replay_export.py trend_pullback
    .venv/Scripts/python.exe tools/family_replay_export.py trend_pullback --symbol EURUSD
    .venv/Scripts/python.exe tools/family_replay_export.py orb_london --bars 2000

One artifact per family *and* market (`family-replay-<family>-<symbol>.json`), so replaying
EURUSD cannot overwrite the USDJPY answer for the same rule.

Prints the payload's summary as the last line of JSON, for the API route to return.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mt5_intel.config import load_settings  # noqa: E402 - path bootstrap above
from mt5_intel.mt5_adapter import ReadOnlyMT5  # noqa: E402
from mt5_intel.research.backtest import (  # noqa: E402
    Bars,
    CLOSED_SL,
    CLOSED_TP,
    closed_r,
    load_bars,
    one_at_a_time,
    plan_trade,
    resolve_offset_s,
    without_forming_bar,
)
from mt5_intel.research.strategies import FAMILIES  # noqa: E402
from mt5_intel.research.strategies_scalp import SCALP_FAMILIES  # noqa: E402

ARTIFACT_DIR = PROJECT_ROOT / "dashboard"
ALL_FAMILIES = [*FAMILIES, *SCALP_FAMILIES]
# The markets a replay can be built for. Both are exported to `user_data/mt5_ohlcv` by the
# same terminal read every other artifact uses, and both are markets the journal filters by,
# so the strategy page can be read the same way the journal is: one market at a time, with the
# choice in the URL.
REPLAY_SYMBOLS = ("USDJPY", "EURUSD")
DEFAULT_BARS = 2_000
DEFAULT_MAX_HOLD = 120


def artifact_path(family: str, symbol: str) -> Path:
    """One artifact per family *and* market.

    USDJPY and EURUSD are different prices, different digits and different rules of thumb, so a
    single file per family would silently overwrite one market with the other the moment a second
    replay ran. The market is part of the name for the same reason it is part of every other
    artifact here.
    """
    return ARTIFACT_DIR / f"family-replay-{family}-{symbol.upper()}.json"


def build_payload(
    bars: Bars,
    family: dict[str, Any],
    *,
    symbol: str,
    timeframe: str,
    max_hold_bars: int,
) -> dict[str, Any]:
    """Bars + one graded row per taken trade. Pure: the caller owns the MT5 connection."""
    sig_i, side, stops, tp_r = (np.asarray(a) for a in family["fn"](bars))
    taken = one_at_a_time(bars, sig_i, side, stops, tp_r, max_hold_bars)
    utc = bars.utc_time()
    digits = bars.digits
    trades: list[dict[str, Any]] = []
    for k, exit_bar, kind in taken:
        i = int(sig_i[k])
        entry_bar = i + 1
        sd = int(side[k])
        plan = plan_trade(bars, i, entry_bar, sd, float(stops[k]), float(tp_r[k]))
        if plan is None:
            continue
        entry = float(plan["entry"])
        sl = float(plan["sl"])
        tp = float(plan["tp"])
        stop_dist = abs(sl - entry)
        if kind == CLOSED_TP:
            exit_price = tp
        elif kind == CLOSED_SL:
            exit_price = sl
        else:
            # time exit and a still-open trade both read the close of their last bar
            exit_price = float(bars.close[-1 if exit_bar is None else int(exit_bar)])
        net_r = (
            None
            if kind == "open"
            else round(
                closed_r(
                    kind,
                    target_r=float(tp_r[k]),
                    cost_r=float(plan["cost_r"]),
                    entry=entry,
                    side=sd,
                    stop_dist=stop_dist,
                    exit_price=exit_price,
                ),
                4,
            )
        )
        trades.append(
            {
                "side": "buy" if sd > 0 else "sell",
                "signal_time": int(utc[i]),
                "entry_time": int(utc[entry_bar]),
                # A still-open trade runs to the newest bar; a closed one to its exit bar.
                "exit_time": int(utc[-1 if exit_bar is None else int(exit_bar)]),
                "entry": round(entry, digits),
                "sl": round(sl, digits),
                "tp": round(tp, digits),
                "exit": round(exit_price, digits),
                # `open` is drawn like a time exit (neutral) - it has not resolved either way.
                "hit": {"open": "time"}.get(kind, kind),
                "still_open": kind == "open",
                "net_r": net_r,
            }
        )
    closed = [t for t in trades if t["net_r"] is not None]
    net = [float(t["net_r"]) for t in closed]
    return {
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "symbol": symbol,
        "timeframe": timeframe,
        "clock": "utc",
        "bars_counted": bars.n,
        "max_hold_bars": max_hold_bars,
        "family": family["id"],
        "digits": bars.digits,
        "bars": [
            {
                "time": int(utc[i]),
                "open": round(float(bars.open_[i]), digits),
                "high": round(float(bars.high[i]), digits),
                "low": round(float(bars.low[i]), digits),
                "close": round(float(bars.close[i]), digits),
            }
            for i in range(bars.n)
        ],
        "trades": trades,
        "summary": {
            "n": len(trades),
            "tp": sum(1 for t in trades if t["hit"] == "tp"),
            "sl": sum(1 for t in trades if t["hit"] == "sl"),
            "time": sum(1 for t in trades if t["hit"] == "time" and not t["still_open"]),
            "open": sum(1 for t in trades if t["still_open"]),
            "net_r_mean": round(float(np.mean(net)), 4) if net else None,
            "net_r_sum": round(float(np.sum(net)), 2) if net else None,
        },
    }


def write_artifact(family: str, symbol: str, payload: dict[str, Any]) -> Path:
    """Atomic write, the way every artifact in this project is written: a reader that
    lands mid-write sees the old file whole, never a truncated one."""
    target = artifact_path(family, symbol)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(target)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("family", choices=sorted(f["id"] for f in ALL_FAMILIES))
    parser.add_argument(
        "--symbol",
        default=None,
        choices=sorted(REPLAY_SYMBOLS),
        help="market to replay (default: the watcher's primary symbol)",
    )
    parser.add_argument("--bars", type=int, default=DEFAULT_BARS)
    parser.add_argument("--timeframe", default="H1", choices=["M5", "M15", "M30", "H1", "H4"])
    parser.add_argument("--max-hold", type=int, default=DEFAULT_MAX_HOLD)
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except Exception as exc:  # noqa: BLE000 - config errors must print, not traceback
        print(f"Config error: {exc}")
        return 2
    try:
        import MetaTrader5 as backend
    except ImportError:
        print("MetaTrader5 package not installed (Windows only).")
        return 2

    family = next(f for f in ALL_FAMILIES if f["id"] == args.family)
    symbol = (args.symbol or settings.symbol_root).upper()
    if symbol not in REPLAY_SYMBOLS:
        print(f"Symbol {symbol} has no replay; known: {', '.join(REPLAY_SYMBOLS)}.")
        return 2
    mt5 = ReadOnlyMT5(backend)
    try:
        mt5.connect(
            login=settings.login,
            password=settings.password,
            server=settings.server,
            terminal_path=settings.terminal_path,
        )
    except ConnectionError as exc:
        print(f"MT5 connect failed: {exc}")
        return 1
    try:
        # The clock offset is resolved per market: the exporter reads the terminal's own
        # server time against UTC for the symbol it is about to export, so two markets on two
        # servers would each get their own correction rather than one inherited from the other.
        offset = resolve_offset_s(mt5, settings, symbol)
        if offset is None:
            print("WARNING: broker clock offset unresolved; stamps are raw server time.")
        bars = load_bars(mt5, symbol, args.timeframe, args.bars, utc_offset_s=offset)
        if bars is None:
            print(f"Insufficient history from terminal for {symbol}; nothing to replay.")
            return 2
        payload = build_payload(
            without_forming_bar(bars),
            family,
            symbol=symbol,
            timeframe=args.timeframe,
            max_hold_bars=args.max_hold,
        )
    finally:
        try:
            mt5.shutdown()
        except Exception:  # noqa: BLE001 - a terminal that will not close is not a replay fault
            pass
    target = write_artifact(args.family, symbol, payload)
    print(
        f"Report: {target} ({symbol} {args.timeframe}, {payload['summary']['n']} trades "
        f"over {payload['bars_counted']} bars)"
    )
    print(json.dumps(payload["summary"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
