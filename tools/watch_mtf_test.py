"""Test probe: what the rules would say on H1, M30 and M15, on the watch symbols.

Not a research grade and not a live view rebuild - a probe. It borrows the exact
code the watcher builds live cards with (`setups.build`), points it at M30/M15
bars, and prints what each strategy would be saying there: how many raw signals
each rule has produced over the loaded window, which cards are live right now,
and how fresh each is against the entry rule the watcher applies. Nothing is
written anywhere the watcher or the auto engine reads; nothing is placed.

The tick registry the watcher trades lives on H1, so every card here is labelled
TICKED / UNTICKED, and two agreeing ticked cards on one bar are named as a
CONFLUENCE-CANDIDATE: a probe of what the M15/M30 view would add, not a claim
that anything would trade from it.

Usage:
    python tools/watch_mtf_test.py                 # one snapshot, all symbols
    python tools/watch_mtf_test.py --loop          # keep watching, print changes
    python tools/watch_mtf_test.py --tf M15        # just one timeframe
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mt5_intel.config import load_settings, watch_symbols
from mt5_intel.mt5_adapter import ReadOnlyMT5
from mt5_intel.research import setups
from mt5_intel.research.backtest import load_bars, resolve_offset_s

# The timeframes to probe; H1 first, as the watch's own baseline to compare against.
TIMEFRAMES_TO_TEST = ("H1", "M30", "M15")
BARS = 601  # the same span the watcher's live view is built from
POLL_S = 30
# The entry freshness rule, restated so a card is legible against it.
MAX_ENTRY_LAG_BARS = 1
CONFLUENCE = "confluence"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SELECTION_FILE = PROJECT_ROOT / "dashboard" / "auto-strategies.json"


def _selection_ticked() -> tuple[str, ...]:
    """The strategies the watcher would trade, read for labelling only.

    A missing or unreadable selection file reads as an empty tick list: the labels
    then name everything UNTICKED, which is the honest reading for a probe.
    """
    try:
        raw = json.loads(SELECTION_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    strategies = raw.get("strategies")
    if not isinstance(strategies, list):
        return ()
    return tuple(str(s) for s in strategies if isinstance(s, str))


def snapshot(
    mt5: ReadOnlyMT5,
    settings: Any,
    symbol: str,
    timeframe: str,
    now_epoch: int,
) -> dict[str, Any] | None:
    """The live card view `setups.build` makes, for one symbol and timeframe."""
    offset_s = resolve_offset_s(mt5, settings, symbol)
    bars = load_bars(mt5, symbol, timeframe, BARS, utc_offset_s=offset_s)
    if bars is None:
        return None
    return setups.build(
        bars,
        timeframe,
        symbol=symbol,
        offset_s=offset_s or 0,
        now_epoch=now_epoch,
    )


def _card_line(row: dict[str, Any]) -> str:
    live = row["status"] == "open" and row.get("side")
    if not live:
        return f"{row['status']:10s} - no live card"
    held = row.get("bars_held")
    signal_utc = row.get("signal_utc") or "?"
    if held is not None and held <= MAX_ENTRY_LAG_BARS:
        return f"{row['status']:10s} {row['side']:4s} since {signal_utc} - FRESH (entry-able by the H1 rule)"
    return f"{row['status']:10s} {row['side']:4s} since {signal_utc} - {held} bars old (past the entry rule)"


def print_report(view: dict[str, Any], timeframe: str, ticked: set[str]) -> None:
    symbol = view.get("symbol", "?")
    asof = view.get("asof_bar_utc", "?")
    print(f"\n=== {symbol} {timeframe}  (as of bar {asof})")
    rows = view["setups"]
    # Confluence candidates: two agreeing ticked cards on one bar, on either
    # timeframe - alive on M15/M30 even though the registry only answers on H1.
    groups: dict[tuple[int, str], list[str]] = {}
    for row in rows:
        if row["status"] == "open" and row.get("side"):
            value = row.get("signal_epoch")
            if isinstance(value, int) and row["family"] in ticked:
                groups.setdefault((value, str(row["side"])), []).append(str(row["family"]))
    for row in rows:
        family = str(row["family"])
        if family in ticked:
            tag = " [TICKED]"
        elif family == CONFLUENCE:
            tag = " [composite]"
        else:
            tag = " [UNTICKED]"
        print(f"  {family:24s}signals={row.get('signals', 0):4d} taken={row.get('taken', 0):3d}{tag}")
        print(f"    {_card_line(row)}")
    fresh_pairs = {key: members for key, members in groups.items() if len(members) >= 2}
    if fresh_pairs:
        print(f"  CONFLUENCE-CANDIDATES on {timeframe}: {len(fresh_pairs)}")
        for (epoch, side), members in sorted(fresh_pairs.items(), reverse=True):
            when = datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%d %H:%M")
            print(f"    {side} at {when}: {' + '.join(sorted(members))}")
    elif rows and any(r["family"] in ticked for r in rows):
        print(f"  no ticked confluence candidates on {timeframe}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tf", default="", help="one timeframe instead of all")
    parser.add_argument("--loop", action="store_true", help="keep polling and print changes")
    args = parser.parse_args()

    timeframes = (args.tf,) if args.tf else TIMEFRAMES_TO_TEST
    for tf in timeframes:
        if tf not in ("M1", "M5", "M15", "M30", "H1"):
            print(f"unrecognized timeframe {tf}", file=sys.stderr)
            return 2

    try:
        settings = load_settings()
    except Exception as exc:  # noqa: BLE001 - the probe reports and stops
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    import MetaTrader5 as backend  # Windows-only, imported after the config check

    mt5 = ReadOnlyMT5(backend)
    try:
        # connect() returns nothing and raises on failure - last_error() is cleared by it.
        mt5.connect(
            login=settings.login,
            password=settings.password,
            server=settings.server,
            terminal_path=settings.terminal_path,
        )
    except ConnectionError as exc:
        print(f"could not connect to the terminal ({exc})", file=sys.stderr)
        return 2

    symbols = watch_symbols(settings)
    ticked = set(_selection_ticked())
    print(f"probing {', '.join(symbols)} on {'/'.join(timeframes)}; ticked: {', '.join(sorted(ticked)) or '(none - H1 selection unreadable)'}")

    seen: dict[str, str] = {}
    try:
        while True:
            now_epoch = int(time.time())
            for symbol in symbols:
                for tf in timeframes:
                    try:
                        view = snapshot(mt5, settings, symbol, tf, now_epoch)
                    except Exception as exc:  # noqa: BLE001 - one read failing is not the probe dying
                        print(f"{symbol} {tf}: read failed ({type(exc).__name__}: {exc})", flush=True)
                        continue
                    if view is None:
                        print(f"{symbol} {tf}: unreadable bars this pass", flush=True)
                        continue
                    payload = json.dumps(view, sort_keys=True, default=str)
                    key = f"{symbol}@{tf}"
                    if not args.loop:
                        print_report(view, tf, ticked)
                        continue
                    if seen.get(key) == payload:
                        continue  # unchanged - say nothing, like the watcher does
                    seen[key] = payload
                    stamp = datetime.fromtimestamp(now_epoch, UTC).strftime("%H:%M:%S")
                    print(f"\n--- {key} changed at {stamp} UTC")
                    print_report(view, tf, ticked)
            if not args.loop:
                return 0
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        print("\nstopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
