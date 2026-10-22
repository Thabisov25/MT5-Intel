"""Ad-hoc: does the ma_crossover card agree with the crossings actually on the chart?

Two questions, one run:

  * every 20/50 crossing in the history, found independently of the family, against the
    signals the family reports on the same bars
  * the card the panel would write right now, next to the crossings that have happened
    since the panel was last written

Usage: python tools/_check_ma_card.py [bars]
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from mt5_intel.config import load_settings
from mt5_intel.mt5_adapter import ReadOnlyMT5
from mt5_intel.research import setups as L
from mt5_intel.research import strategies as S
from mt5_intel.research.backtest import load_bars, resolve_offset_s

ARTIFACT = Path(__file__).resolve().parents[1] / "dashboard" / "setups.json"


def utc(epoch: int, offset_s: int) -> str:
    return datetime.fromtimestamp(int(epoch) - offset_s, UTC).strftime("%Y-%m-%d %H:%M")


def main() -> int:
    n_bars = int(sys.argv[1]) if len(sys.argv) > 1 else 20_000
    settings = load_settings()
    import MetaTrader5 as backend

    mt5 = ReadOnlyMT5(backend)
    mt5.connect(
        login=settings.login,
        password=settings.password,
        server=settings.server,
        terminal_path=settings.terminal_path,
    )
    try:
        offset = resolve_offset_s(mt5, settings, settings.symbol_root) or 0
        bars = load_bars(mt5, settings.symbol_root, "H1", n_bars, utc_offset_s=offset)
    finally:
        mt5.shutdown()
    if bars is None:
        print("no history")
        return 2

    print(f"{settings.symbol_root} H1, {bars.n} bars, offset {offset} s")
    print(f"  last bar (forming) {utc(bars.time[-1], offset)}   last closed {utc(bars.time[-2], offset)}")
    print(f"  close {bars.close[-1]:.3f}")

    # The crossings, computed from the raw averages and nothing else.
    sep = S._sma(bars.close, 20) - S._sma(bars.close, 50)
    crossings = []
    prev = 0
    for i in range(bars.n - 1):  # closed bars only
        if not np.isfinite(sep[i]) or sep[i] == 0:
            continue
        s = 1 if sep[i] > 0 else -1
        if prev and s != prev:
            crossings.append((i, s))
        prev = s
    family = next(f for f in S.FAMILIES if f["id"] == "ma_crossover")
    sig_i, side, stops, _tp_r = (np.asarray(a) for a in family["fn"](bars))
    print(f"\n  independent crossings: {len(crossings)}   family signals: {sig_i.size}")
    only_cross = {int(i) for i, _ in crossings} - {int(i) for i in sig_i}
    only_sig = {int(i) for i in sig_i} - {int(i) for i, _ in crossings}
    print(f"  on the chart but not in the family: {sorted(only_cross)}")
    print(f"  in the family but not on the chart: {sorted(only_sig)}")

    print("\n  last 6 crossings (independent):")
    for i, s in crossings[-6:]:
        print(f"    {utc(bars.time[i], offset)}  {'up' if s > 0 else 'down'}  sep {sep[i] / bars.pip():+.1f} pips")
    print("\n  last 6 family signals:")
    for k in range(max(0, sig_i.size - 6), sig_i.size):
        i = int(sig_i[k])
        print(
            f"    {utc(bars.time[i], offset)}  {'buy' if side[k] > 0 else 'sell'}  "
            f"stop {stops[k]:.1f} pips  ({bars.n - 1 - i} bars ago)"
        )
    pip = bars.pip()
    print(f"\n  separation now {sep[bars.n - 2] / pip:+.1f} pips on the last closed bar")

    # A chart's own indicator is drawn including the bar still forming, and it may not be the
    # 20/50 pair at all: this is what the reader could be looking at that the panel is not.
    print("\n  other pairs, last crossing on closed bars, and separation now:")
    for label, kind, fast, slow in (
        ("20/50 SMA", "sma", 20, 50),
        ("9/21 EMA", "ema", 9, 21),
        ("50/200 EMA", "ema", 50, 200),
        ("50/200 SMA", "sma", 50, 200),
    ):
        ma = S._sma if kind == "sma" else S._ema
        d = ma(bars.close, fast) - ma(bars.close, slow)
        last, sign, prev = None, 0, 0
        for i in range(bars.n - 1):
            if not np.isfinite(d[i]) or d[i] == 0:
                continue
            s = 1 if d[i] > 0 else -1
            if prev and s != prev:
                last, sign = i, s
            prev = s
        where = "-" if last is None else f"{utc(bars.time[last], offset)} {'up' if sign > 0 else 'down'}"
        forming = d[bars.n - 1] / pip if np.isfinite(d[bars.n - 1]) else float("nan")
        flipped_intrabar = (
            np.isfinite(d[bars.n - 1]) and np.isfinite(d[bars.n - 2])
            and (d[bars.n - 1] > 0) != (d[bars.n - 2] > 0)
        )
        print(
            f"    {label:<10} last cross {where:<22} sep closed {d[bars.n - 2] / pip:+7.1f} "
            f"with the forming bar {forming:+7.1f} pips"
            + ("   <- the two lines have crossed since the last close" if flipped_intrabar else "")
        )

    # Price against a single line, which is what "the candles crossed the average" means.
    # It is not a signal in this family at all - the rule trades one line crossing *another* -
    # but it is the event a reader most often mistakes for the cross, so it is named here.
    print("\n  price vs a single line (not a signal - the family crosses two lines):")
    for label, kind, period in (
        ("SMA20", "sma", 20),
        ("SMA50", "sma", 50),
        ("EMA50", "ema", 50),
        ("EMA200", "ema", 200),
    ):
        ma = S._sma if kind == "sma" else S._ema
        line = ma(bars.close, period)
        last, side_i, prev = None, 0, 0
        for i in range(bars.n - 1):
            if not np.isfinite(line[i]):
                continue
            s = 1 if bars.close[i] > line[i] else -1
            if prev and s != prev:
                last, side_i = i, s
            prev = s
        where = "-" if last is None else f"{utc(bars.time[last], offset)} {'above' if side_i > 0 else 'below'}"
        now = (bars.close[bars.n - 2] - line[bars.n - 2]) / pip
        print(f"    {label:<7} last close-through {where:<22} price now {now:+7.1f} pips vs the line")

    # What the panel writes if it refreshes now, against what is on disk.
    card = L.latest_setup(bars, family, offset_s=offset)
    print(f"\n  card now: {json.dumps(card, default=str)}")
    if ARTIFACT.exists():
        on_disk = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        old = next(r for r in on_disk["setups"] if r["family"] == "ma_crossover")
        print(f"  card on disk ({on_disk['generated_utc']}, asof {on_disk['asof_bar_utc']}): "
              f"{old['side']} signal {old['signal_utc']} {old['status']} closed {old['closed_utc']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
