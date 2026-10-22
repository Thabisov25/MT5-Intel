"""Dated checkpoint reproduction: does our data and signals match the sources?

Usage: python -m mt5_intel.research.checkpoints

Loads real D1 history and verifies the researched dated checkpoints (Fed H.10
noon rates, BIS/CNBC levels) within tolerance, then reports what our two new
families would have signaled across each crash window. Exit 0 when every
checkpoint matches; nonzero otherwise, so a wrong data source fails loudly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import numpy as np

from ..config import ConfigError, load_settings
from ..mt5_adapter import ReadOnlyMT5
from .backtest import TIMEFRAMES, load_bars
from .rates import carry_signed_spread
from .strategies import FAMILIES


def _epoch(date_str: str) -> int:
    return int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC).timestamp())


def _close_on_or_before(bars: Any, date_str: str) -> tuple[int, float] | None:
    ts = _epoch(date_str)
    idx = int(np.searchsorted(bars.time, ts, side="right")) - 1
    if idx < 0:
        return None
    return int(bars.time[idx]), float(bars.close[idx])


# (label, date, expected H.10-style rate, tolerance)
H10_CHECKPOINTS: tuple[tuple[str, str, float, float], ...] = (
    ("2024 unwind start", "2024-07-31", 150.38, 1.5),   # H.10 noon; broker close may differ
    ("2024 unwind trough", "2024-08-05", 143.95, 1.5),
    ("2007 unwind start", "2007-08-01", 118.71, 1.5),
    ("2007 unwind trough", "2007-08-16", 114.03, 1.5),
)

# Crash-window signal expectations from the sources (direction of the carry trade).
CARRY_WINDOWS: tuple[tuple[str, str, str], ...] = (
    ("2024 BoJ unwind", "2024-07-25", "2024-08-05"),
    ("2007 carry unwind", "2007-08-01", "2007-08-16"),
)


def _window_return(bars: Any, start: str, end: str) -> float | None:
    a = _close_on_or_before(bars, start)
    b = _close_on_or_before(bars, end)
    if a is None or b is None:
        return None
    return (b[1] - a[1]) / a[1]


def run() -> int:
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
    if "D1" not in TIMEFRAMES:
        print("D1 not allow-listed")
        return 2

    mt5 = ReadOnlyMT5(backend)
    mt5.connect(
        login=settings.login,
        password=settings.password,
        server=settings.server,
        terminal_path=settings.terminal_path,
    )
    failures = 0
    try:
        bars = load_bars(mt5, settings.symbol_root, "D1", 80_000)
        if bars is None:
            print("No D1 history")
            return 2
        first = datetime.fromtimestamp(int(bars.time[0]), UTC).date()
        last = datetime.fromtimestamp(int(bars.time[-1]), UTC).date()
        print(f"D1 coverage: {first} .. {last} ({bars.n} bars)")

        # 1. H.10 level checkpoints within tolerance (noon vs close differences).
        for label, date, expected, tol in H10_CHECKPOINTS:
            hit = _close_on_or_before(bars, date)
            if hit is None:
                print(f"  CHECKPOINT MISS {label} {date}: no bar at or before")
                failures += 1
                continue
            got = hit[1]
            delta = abs(got - expected)
            status = "OK" if delta <= tol else "MISMATCH"
            if delta > tol:
                failures += 1
            print(f"  {status:8s} {label:22s} {date} expected {expected:.2f} "
                  f"got {got:.2f} (delta {delta:.2f}, tol {tol})")

        # 2. Carry direction + realized move across each crash window.
        for label, start, end in CARRY_WINDOWS:
            spread = carry_signed_spread(_epoch(start))
            direction = 1 if spread > 0 else (-1 if spread < 0 else 0)
            ret = _window_return(bars, start, end)
            if ret is None:
                print(f"  CHECKPOINT MISS {label}: window outside data")
                failures += 1
                continue
            # A long-carry position (direction=+1) is hurt when USDJPY falls.
            pnl_sign = "ADVERSE" if (direction > 0 and ret < 0) or (direction < 0 and ret > 0) else "FRIENDLY"
            print(f"  {label}: carry spread {spread:+.3f}% -> direction {direction:+d}; "
                  f"USDJPY {start}..{end} {ret:+.2%} -> {pnl_sign} to the carry position")

        # 3. Signal behavior across the 2024 window for every family (GAP-4: the
        # in-house families have no published dated examples, so this checks the
        # harness is sane on a known event rather than reproducing a source).
        w0, w1 = _epoch("2024-06-01"), _epoch("2024-09-30")
        for family in FAMILIES:
            sig, side, _, _ = family["fn"](bars)
            mask = (sig >= w0) & (sig <= w1)
            flips = [
                (
                    datetime.fromtimestamp(int(bars.time[int(sig[k])]), UTC).date().isoformat(),
                    int(side[k]),
                )
                for k in np.nonzero(mask)[0]
            ]
            shown = flips[:8] + (["..."] if len(flips) > 8 else [])
            print(f"  {family['id']:20s} signals Jun-Sep 2024: {len(flips)} {shown if shown else ''}")
    finally:
        mt5.shutdown()

    print(f"{'ALL CHECKPOINTS MATCH' if failures == 0 else f'{failures} CHECKPOINT FAILURES'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run())
