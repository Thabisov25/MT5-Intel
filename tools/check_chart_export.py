"""End-to-end check on the real files: everything the chart indicator reads, checked against the
terminal's own bars. Not a test in the suite - it needs the live terminal - so it lives here."""

from __future__ import annotations

import csv
from pathlib import Path

from mt5_intel.config import load_settings
from mt5_intel.mt5_adapter import ReadOnlyMT5
from mt5_intel.research import export_mt5, setups
from mt5_intel.research.backtest import load_bars, resolve_offset_s

TF = "H1"


def main() -> int:
    settings = load_settings()
    import MetaTrader5 as backend

    mt5 = ReadOnlyMT5(backend)
    mt5.connect(
        login=settings.login,
        password=settings.password,
        server=settings.server,
        terminal_path=settings.terminal_path,
    )
    failures: list[str] = []
    try:
        bars = load_bars(
            mt5,
            settings.symbol_root,
            TF,
            20_000,
            utc_offset_s=resolve_offset_s(mt5, settings, settings.symbol_root),
        )
        assert bars is not None
        folder = Path(mt5.terminal_data_path()) / "MQL5" / "Files"

        with (folder / export_mt5.lines_filename(TF)).open(encoding="utf-8", newline="") as handle:
            lines = list(csv.DictReader(handle))
        with (folder / export_mt5.context_filename(TF)).open(
            encoding="utf-8", newline=""
        ) as handle:
            ctx = dict(csv.reader(handle))

        bar_stamps = {export_mt5._stamp(int(t)) for t in bars.time}
        off = [row["time"] for row in lines if row["time"] not in bar_stamps]
        print(f"lines rows           : {len(lines)}")
        print(f"  rows off a real bar: {len(off)}")
        if off:
            failures.append(f"line rows not on bars: {off[:3]}")
        consecutive = all(
            export_mt5._stamp(int(t)) == row["time"]
            for t, row in zip(bars.time[-len(lines) :], lines, strict=True)
        )
        print(f"  newest {len(lines)} bars, in order, one row each: {consecutive}")
        if not consecutive:
            failures.append("line rows are not the newest contiguous bars")

        # The panel's numbers must be the ones the rules were read against.
        ctx_read = setups.market_context(bars, offset_s=3 * 3600)
        checks = {
            "trend": ctx_read["ema_trend"],
            "ema_fast": f"{ctx_read['ema_fast']:.{bars.digits}f}",
            "ema_slow": f"{ctx_read['ema_slow']:.{bars.digits}f}",
            "sma_fast": f"{ctx_read['sma_fast']:.{bars.digits}f}",
            "atr_pips": f"{ctx_read['atr_pips']:.1f}",
            "range_pos_pct": f"{ctx_read['range_pos'] * 100:.0f}",
        }
        for key, want in checks.items():
            got = ctx[key]
            ok = got == want
            print(f"context {key:<16}: {got!r} == market_context {want!r}: {ok}")
            if not ok:
                failures.append(f"context {key}: {got!r} != {want!r}")

        # The line on the last closed bar and the number the panel prints are one calculation.
        last_closed = lines[-2]
        same = float(last_closed["ema_fast"]) == ctx_read["ema_fast"]
        print(f"line vs panel (EMA fast, last closed bar): {same}")
        if not same:
            failures.append("the drawn EMA and the quoted EMA disagree")

        # The agreement column the chart paints from: on the real file, it has to be the live
        # cards on that row's bar and side - a count off by a side would paint a disagreement.
        with (folder / export_mt5.filename(TF)).open(encoding="utf-8", newline="") as handle:
            trades = list(csv.DictReader(handle))
        live = [row for row in trades if row["state"] == "open"]

        def live_here(row: dict[str, str]) -> int:
            """Live cards on this row's bar, in its direction - what the chart paints from."""
            return len(
                {
                    other["family"]
                    for other in live
                    if (other["entry_time"], other["side"]) == (row["entry_time"], row["side"])
                }
            )

        wrong = [row["family"] for row in live if int(row["confluence"]) != live_here(row)]
        print(f"trades rows          : {len(trades)} ({len(live)} running)")
        print(f"  agreement miscounts: {len(wrong)}")
        if wrong:
            failures.append(f"agreement counts wrong: {wrong[:3]}")

        print(f"rsi in context       : {ctx.get('rsi')} (period {ctx.get('rsi_period')})")
        if not ctx.get("rsi"):
            failures.append("no RSI in the context file")
    finally:
        mt5.shutdown()

    print()
    if failures:
        for item in failures:
            print(f"FAIL {item}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
