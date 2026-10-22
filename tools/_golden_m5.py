"""Separate the two window fixes on real bars: the clock, and the duration.

Two things were wrong and they change different things, so checking them together would hide
one behind the other:

  * the session hour was read off the raw stamp (the BROKER's clock), so "07:00 UTC" was 04:00
    UTC here. Reproducing that old reading is just `utc_offset_s=0` on real bars, which is what
    `old` below does;
  * the window was a count of bars written for M5, so a "30-minute" opening range was six hours
    on H1. On M5 a 30-minute range is exactly six bars either way, so the duration fix is a
    no-op there by construction - which is the point of running it here.

M5 is the timeframe these templates are written for, so the duration half must not move it.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from mt5_intel.config import load_settings
from mt5_intel.mt5_adapter import ReadOnlyMT5
from mt5_intel.research.backtest import load_bars, resolve_offset_s
from mt5_intel.research.strategies_scalp import asian_fade, orb_london

RULES = {"orb_london": orb_london, "asian_fade": asian_fade}
BAR_COUNT = 20_000


def collect() -> dict[str, dict[str, list[int]]]:
    settings = load_settings()
    import MetaTrader5 as backend

    mt5 = ReadOnlyMT5(backend)
    mt5.connect(
        login=settings.login,
        password=settings.password,
        server=settings.server,
        terminal_path=settings.terminal_path,
    )
    offset = resolve_offset_s(mt5, settings, settings.symbol_root)
    out: dict[str, dict[str, list[int]]] = {}
    try:
        print(f"broker offset from UTC: {offset} s")
        for timeframe in ("M5", "M15"):
            for label, use_offset in (("old clock", 0), ("corrected", offset)):
                bars = load_bars(
                    mt5,
                    settings.symbol_root,
                    timeframe,
                    BAR_COUNT,
                    utc_offset_s=use_offset,
                )
                assert bars is not None, timeframe
                out[f"{timeframe} {label}"] = {}
                for name, fn in RULES.items():
                    sig_i, _side, stops, _tp = (np.asarray(a) for a in fn(bars))
                    out[f"{timeframe} {label}"][name] = [int(i) for i in sig_i]
                    median = float(np.median(stops)) if stops.size else float("nan")
                    print(f"  {timeframe:>3} {label:<10} {name:<12} {sig_i.size:>4} signals, "
                          f"median stop {median:6.2f} pips")
    finally:
        mt5.shutdown()
    return out


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "before"
    path = Path(tempfile.gettempdir()) / "mt5intel_m5_golden.json"
    current = collect()
    if mode == "before":
        path.write_text(__import__("json").dumps(current), encoding="utf-8")
        print(f"baseline written to {path}")
        return 0

    import json

    before: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    print()
    # The stored baseline was written by the old code, which is exactly "read the hour off the
    # stamp" - so it has to match this run's old-clock pass bar for bar. That equality is what
    # proves the duration fix moved nothing on M5, leaving the clock as the only change.
    failures = 0
    for timeframe in ("M5", "M15"):
        for name in RULES:
            baseline = before[timeframe][name]
            old_clock = current[f"{timeframe} old clock"][name]
            corrected = current[f"{timeframe} corrected"][name]
            same = baseline == old_clock
            print(f"  {timeframe:>3} {name:<12} baseline {len(baseline):>3} == old clock "
                  f"{len(old_clock):>3}: {same}   |   corrected clock: {len(corrected)} signals")
            # M5 is where a 30-minute range is the same six bars before and after, so only M5
            # can be compared bar for bar. On M15 the duration fix changes the old-clock pass
            # too (6 bars of 90 minutes became 2 bars of 30), which is the other half landing.
            if timeframe == "M5" and not same:
                failures += 1
                print("     ^ M5 must reproduce the baseline: the duration fix is a no-op there")
    print()
    if failures:
        print("FAILED: M5 no longer reproduces the baseline")
        return 1
    print("M5 reproduced exactly under the old clock; only the clock fix moves it there")
    print("(M15 differs in both passes: that is the six-bar window becoming 30 real minutes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
