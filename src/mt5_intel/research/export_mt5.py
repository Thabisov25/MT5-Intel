"""Put the trades this console actually took onto the MT5 chart, so they can be looked at.

Why the trades are computed here and merely *drawn* in MQL5: the families live in Python and stay
the only place they are defined. An indicator that recomputed them would be a second
implementation, free to drift out of step - and then a chart disagreeing with the console could
not tell you which of the two was wrong, which is the one question this is meant to answer. So
this writes the trades the harness took (one position at a time, D-035) and `Mt5IntelSignals.mq5`
only draws what it finds here.

Times are written on the **broker's clock**, not UTC. That is the clock the terminal draws on the
chart, and the indicator matches rows to bars by that stamp, so converting to UTC here would put
every arrow on the wrong bar. (Nothing in this project trades off these files; they are for eyes.)

Three files land side by side, all from the same bars, named per symbol and per timeframe:

* `mt5intel_<SYMBOL>_<TF>.csv` - the trades, one row per position taken. Its last column counts
  the live cards that entered on the same bar, in the same direction, which is what the chart
  paints green.
* `mt5intel_<SYMBOL>_<TF>_lines.csv` - the backdrop the rules were read against, bar by bar: EMA(50),
  EMA(200), SMA(20), SMA(50). Written here rather than left to MQL5's own `iMA()`, because
  MT5 seeds an EMA off the first bars of whatever history it happens to have loaded, so a chart
  line could sit a hair away from the number the card quotes with no way to tell which was right.
* `mt5intel_<SYMBOL>_<TF>_context.csv` - the same backdrop as `setups.market_context` reads it at
  the last closed bar, as `key,value` pairs the indicator prints as a panel.

The names carry the symbol because the indicator reads them off its own chart (`_Symbol`), so a
chart of another symbol cannot mistake another symbol's trades for its own - which it did, once,
when the files were named per timeframe only.

Every file opens with one stamp line, `#rows,<n>,<n>`: how many rows it holds, written twice so a
stamp caught half-written cannot claim the rows it was cut after. The indicator trusts a file only
when that count holds, so a read that lands inside a rewrite is dropped whole and the rows already
on the chart stay - which is what keeps the arrows, the lines and the key from flickering between
two half-reads of one export.

Usage:
  python -m mt5_intel.research.export_mt5 [timeframe] [--bars 20000] [--per-family 60] [--out DIR]

`--out` defaults to the terminal's own `MQL5\\Files`, which is where the indicator reads from.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from ..config import ConfigError, Settings, load_settings
from ..datachecks import estimate_server_offset_s, offset_is_plausible
from ..mt5_adapter import ReadOnlyMT5
from .backtest import (
    TIMEFRAMES,
    Bars,
    bars_held,
    closed_r,
    load_bars,
    one_at_a_time,
    plan_trade,
    refires_while_open,
    without_forming_bar,
)
from .replay import DEFAULT_MAX_HOLD, families_for
from .setups import (
    CONTEXT_ATR_PERIOD,
    CONTEXT_CHANGES,
    CONTEXT_EMA_FAST,
    CONTEXT_EMA_SLOW,
    CONTEXT_SMA_FAST,
    CONTEXT_SMA_SLOW,
    market_context,
)
from .strategies import Family, _ema, _sma

DEFAULT_BARS = 20_000
DEFAULT_PER_FAMILY = 60
DEFAULT_LINE_BARS = 400
ML_DIR = Path("MQL5") / "Files"

# Order the indicator reads by position, so the header and the rows cannot disagree.
HEADER: tuple[str, ...] = (
    "family",
    "side",
    "entry_time",
    "entry",
    "sl",
    "tp",
    "exit_time",
    "result_r",
    "state",
    "resignals",
    "bars_held",
    # Appended after the columns the indicator requires, so a trades file written before this
    # existed still parses there (COL_CONFLUENCE) and simply draws no agreement.
    "confluence",
)

# The averages drawn on the chart, in the order the indicator expects them.
LINES_HEADER: tuple[str, ...] = ("time", "ema_fast", "ema_slow", "sma_fast", "sma_slow")


def _stamp(epoch: int) -> str:
    """The bar's own clock reading - the one the chart shows, not UTC."""
    return datetime.fromtimestamp(int(epoch), UTC).strftime("%Y.%m.%d %H:%M")


def _rows_stamp(row_count: int) -> str:
    """The completeness stamp every chart file opens with: `#rows,<n>,<n>`, the count twice.

    Twice on purpose. A reader that lands inside a rewrite sees a prefix of the new text, and a
    stamp cut in half - `#rows,482,48` - would otherwise claim exactly the rows it happened to be
    cut after. With both copies present a cut stamp cannot agree with itself, and a count that
    does not match the rows below it is a file caught mid-write, not an export.
    """
    return f"#rows,{row_count},{row_count}\r\n"


def _rounded(value: float | None) -> float | None:
    """Four decimals of R is past the point of meaning, and the file is read by eye and by MQL5."""
    return None if value is None else round(float(value), 4)


def filename(timeframe: str, symbol: str) -> str:
    """Per symbol first: the indicator reads this off its own chart, so USDJPY and EURUSD never
    draw each other's arrows."""
    return f"mt5intel_{symbol}_{timeframe}.csv"


def lines_filename(timeframe: str, symbol: str) -> str:
    return f"mt5intel_{symbol}_{timeframe}_lines.csv"


def context_filename(timeframe: str, symbol: str) -> str:
    return f"mt5intel_{symbol}_{timeframe}_context.csv"


def _family_signals(bars: Bars, family: Family) -> tuple[np.ndarray, ...] | None:
    """One rule's signals, or None if it raised: a broken rule must not cost the others a chart."""
    try:
        return tuple(np.asarray(a) for a in family["fn"](bars))
    except Exception:  # noqa: BLE001 - its absence from the chart is the report
        return None


def _mark_confluence(rows: list[dict[str, Any]]) -> None:
    """How many distinct rules are live on each row's bar, in its direction, counting itself.

    This is the one thing a single row cannot say and a chart can: two cards agreeing. It is
    computed here rather than in MQL5 for the same reason the trades are - the rules live in
    Python, and the indicator only draws what it is told. Only running rows take part (a finished
    trade, and a current-signal row - which is a plan and not a taken trade - are not live
    alongside anything) and only the same side counts (a buy and a sell on one bar are a
    disagreement, not an agreement), so 1 means "alone" and 2 or more is the pair the chart paints.
    """
    live: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        if row["state"] != "open":
            continue
        live.setdefault((str(row["entry_time"]), str(row["side"])), set()).add(str(row["family"]))
    for row in rows:
        key = (str(row["entry_time"]), str(row["side"]))
        row["confluence"] = len(live[key]) if row["state"] == "open" else 1


def _signal_row(
    bars: Bars,
    family: Family,
    sig_i: np.ndarray,
    side: np.ndarray,
    stops: np.ndarray,
    tp_r: np.ndarray,
    jk: int,
) -> dict[str, Any] | None:
    """One stood-aside signal as a chart row (`state="signal"`), or None when unplannable.

    A current reading is a plan, never a taken trade: no exit, no result, only what the rule is
    asking for now. The plan reads the FULL bars - a signal on the last closed bar enters on the
    forming candle's open, the price a market order meets right now - while the signal itself
    came from closed bars, so the row's bar is stable within the candle.
    """
    ji = int(sig_i[jk])
    jsd = int(side[jk])
    j_entry_bar = ji + 1
    plan = plan_trade(bars, ji, j_entry_bar, jsd, float(stops[jk]), float(tp_r[jk]))
    if plan is None:
        return None
    return {
        "family": family["id"],
        "side": "buy" if jsd > 0 else "sell",
        "entry_time": _stamp(int(bars.time[j_entry_bar])),
        "entry": round(float(plan["entry"]), bars.digits),
        "sl": round(float(plan["sl"]), bars.digits),
        "tp": round(float(plan["tp"]), bars.digits),
        "exit_time": "",
        "result_r": None,
        "state": "signal",
        "resignals": 0,
        "bars_held": bars_held(j_entry_bar, None, bars.n),
    }


def _current_signal_row(
    bars: Bars,
    family: Family,
    sig_i: np.ndarray,
    side: np.ndarray,
    stops: np.ndarray,
    tp_r: np.ndarray,
    holder_k: int,
    exit_bar: int | None,
) -> dict[str, Any] | None:
    """The rule's newest stood-aside signal as a chart row, or None when it has nothing newer.

    A rule holding one position re-fires while its condition continues, possibly on the opposite
    side, and until now the chart only ever showed the trade it took - so the very moment a
    viewer would want to know what the rule is saying *now* is exactly what the file omitted.
    The row carries `state="signal"` so the indicator draws it as a current reading, never as a
    taken trade or a counted result.
    """
    if exit_bar is not None:
        return None
    live_until = bars.n - 1
    holder_bar = int(sig_i[holder_k])
    entry_bar = holder_bar + 1
    later = [
        j for j in range(int(sig_i.size)) if entry_bar < int(sig_i[j]) + 1 <= live_until
    ]
    if not later:
        return None
    return _signal_row(bars, family, sig_i, side, stops, tp_r, later[-1])


def build_rows(
    bars: Bars,
    timeframe: str,
    per_family: int = DEFAULT_PER_FAMILY,
    max_hold_bars: int = DEFAULT_MAX_HOLD,
) -> list[dict[str, Any]]:
    """The trades each rule took, newest `per_family` of them, oldest first.

    The same `one_at_a_time` decision the Live tab and the replays read, so a chart drawn from
    this cannot show a trade the console would not count - and a rule that re-fired every bar
    appears once, as the position it was, rather than as a row per bar.

    The signals and the trade walks run on CLOSED bars only (`without_forming_bar`): the last
    bar from the terminal is mid-candle, and grading it here would move an arrow within the
    candle - on, then off, then on again - exactly the flicker this file exists not to have.
    """
    rows: list[dict[str, Any]] = []
    closed = without_forming_bar(bars)
    for family in families_for(timeframe):
        signals = _family_signals(closed, family)
        if signals is None:
            continue
        sig_i, side, stops, tp_r = signals
        if sig_i.size == 0:
            continue
        taken = one_at_a_time(closed, sig_i, side, stops, tp_r, max_hold_bars)
        running = next((t for t in reversed(taken) if t[1] is None), None)
        if running is not None:
            # The current reading of a rule that is holding: its newest stood-aside signal,
            # which is what the rule is asking for now - not history.
            signal_row = _current_signal_row(
                bars, family, sig_i, side, stops, tp_r, running[0], running[1]
            )
            if signal_row is not None:
                rows.append(signal_row)
        else:
            # A flat rule whose newest signal sits on the very last closed bar is asking to take
            # now - its entry is the forming candle's open. Drawn as the same current reading
            # (state `signal`), so the live arrow the Live tab is showing as its take-now card
            # is one the chart shows too; planned on the full bars, never walked inside the
            # candle, so it cannot flip to target or stop before the entry bar has closed.
            last_closed = closed.n - 1
            newest = [j for j in range(int(sig_i.size)) if int(sig_i[j]) == last_closed]
            if newest:
                flat_row = _signal_row(bars, family, sig_i, side, stops, tp_r, newest[-1])
                if flat_row is not None:
                    rows.append(flat_row)
        for k, exit_bar, kind in taken[-per_family:]:
            i = int(sig_i[k])
            sd = int(side[k])
            entry_bar = i + 1
            plan = plan_trade(bars, i, entry_bar, sd, float(stops[k]), float(tp_r[k]))
            if plan is None:
                continue
            entry = float(plan["entry"])
            stop_dist = abs(float(plan["sl"]) - entry)
            live_until = bars.n - 1 if exit_bar is None else int(exit_bar)
            rows.append(
                {
                    "family": family["id"],
                    "side": "buy" if sd > 0 else "sell",
                    "entry_time": _stamp(int(bars.time[entry_bar])),
                    "entry": round(entry, bars.digits),
                    "sl": round(float(plan["sl"]), bars.digits),
                    "tp": round(float(plan["tp"]), bars.digits),
                    "exit_time": "" if exit_bar is None else _stamp(int(bars.time[int(exit_bar)])),
                    "result_r": _rounded(
                        closed_r(
                            kind,
                            target_r=float(tp_r[k]),
                            cost_r=float(plan["cost_r"]),
                            entry=entry,
                            side=sd,
                            stop_dist=stop_dist,
                            exit_price=None if exit_bar is None else float(bars.close[int(exit_bar)]),
                        )
                    ),
                    "state": "open" if exit_bar is None else "closed",
                    "resignals": refires_while_open(sig_i, k, entry_bar, live_until),
                    "bars_held": bars_held(entry_bar, exit_bar, bars.n),
                }
            )
    _mark_confluence(rows)
    rows.sort(key=lambda row: (str(row["entry_time"]), str(row["family"])))
    return rows


def _write_text_atomically(path: Path, text: str) -> None:
    """Put one whole file under the name the chart reads.

    The indicator re-reads these files on a timer while the exporter is rewriting them, and a
    read caught inside an in-place rewrite sees a truncation: file ends cleanly, says nothing,
    and the chart deletes every object until the next read. The old in-place `open("w")` is what
    made the chart flash blank around every export. Writing a sibling temp file and then
    renaming it over the target means the name always holds a whole file - the previous one or
    the new one, never half of either. A terminal that keeps the old file open blocks the
    rename; after a short retry the writings fall back in place rather than let the chart
    starve.
    """
    # A name per write: the watcher and a hand-run CLI can export the same chart at the same
    # moment, and two writers sharing one temp name interleave their texts before one of them
    # renames the mangled result into place - a whole name holding half a file. (The stamps the
    # files carry catch that on the read side; this stops it being written at all.)
    tmp = path.with_name(f"{path.stem}.writing{os.getpid()}.{time.time_ns()}{path.suffix}")
    tmp.write_text(text, encoding="utf-8", newline="")
    try:
        for _ in range(4):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                time.sleep(0.25)
        path.write_text(text, encoding="utf-8")
    finally:
        tmp.unlink(missing_ok=True)


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    """Write the rows with an explicit header; blank cells mean \"still open\" / \"not finished\"."""
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO(newline="")
    buffer.write(_rows_stamp(len(rows)))
    writer = csv.DictWriter(buffer, fieldnames=list(HEADER), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        written = dict(row)
        if written.get("result_r") is None:
            written["result_r"] = ""
        writer.writerow(written)
    _write_text_atomically(path, buffer.getvalue())


def _cell(value: float | None, digits: int) -> str:
    """A number for the chart, or an empty cell where the average has not warmed up yet."""
    return "" if value is None else f"{float(value):.{digits}f}"


def build_lines(bars: Bars, window: int = DEFAULT_LINE_BARS) -> list[dict[str, str]]:
    """The backdrop the rules read, bar by bar, so the chart can draw the same lines.

    `market_context` quotes these averages at one bar; this is the same two EMAs and two SMAs
    across the last `window` bars. Drawn from here rather than left to MT5's own `iMA()`, which
    seeds an EMA off the first bars of whatever history the chart happens to hold: the chart could
    then show a line a hair away from the number beside it with no way to tell which was right.

    The forming bar is included - a trader's moving average sits on the live candle - and a row
    whose average has not warmed up carries a blank cell rather than a made-up number.
    """
    if bars.n == 0:
        return []
    digits = bars.digits
    ema_fast = _ema(bars.close, CONTEXT_EMA_FAST)
    ema_slow = _ema(bars.close, CONTEXT_EMA_SLOW)
    sma_fast = _sma(bars.close, CONTEXT_SMA_FAST)
    sma_slow = _sma(bars.close, CONTEXT_SMA_SLOW)

    def cell(series: np.ndarray, i: int) -> str:
        v = float(series[i])
        return _cell(v if np.isfinite(v) else None, digits)

    rows: list[dict[str, str]] = []
    for i in range(max(0, bars.n - window), bars.n):
        rows.append(
            {
                "time": _stamp(int(bars.time[i])),
                "ema_fast": cell(ema_fast, i),
                "ema_slow": cell(ema_slow, i),
                "sma_fast": cell(sma_fast, i),
                "sma_slow": cell(sma_slow, i),
            }
        )
    return rows


def build_context(
    bars: Bars, timeframe: str, symbol: str, offset_s: int | None = None
) -> list[tuple[str, str]]:
    """The backdrop as `setups.market_context` reads it, flattened to `key,value` pairs.

    The same function the Live tab's cards are read against, so the panel on the chart and the
    panel in the browser cannot quote different numbers for the same bar. Values arrive here
    pre-formatted because they are read by eye down one column; the indicator only sets layout.

    With no known offset the stamp says so and is left on the broker's clock, which is the clock
    the chart under it is drawn on: a reading labelled "UTC" that is not UTC would be worse than
    no label at all.
    """
    utc_known = bool(offset_s)
    ctx = market_context(bars, int(offset_s or 0))
    pip = bars.pip()
    digits = bars.digits
    i = max(0, bars.n - 2)
    as_of = (
        f"{ctx['last_closed_utc']} UTC"
        if utc_known
        else f"{_stamp(int(bars.time[i]))} broker clock"
    )

    def num(value: Any, digits_: int = 1) -> str:
        return "?" if value is None else f"{float(value):.{digits_}f}"

    def px(value: Any) -> str:
        return "?" if value is None else f"{float(value):.{digits}f}"

    def yes(value: Any) -> str:
        return "?" if value is None else ("yes" if value else "no")

    def signed(value: Any) -> str:
        return "?" if value is None else f"{float(value):+.1f}"

    sep = ctx.get("ema_sep_pips")
    return [
        ("symbol", symbol),
        ("timeframe", timeframe),
        ("as_of", as_of),
        ("clock", "utc" if utc_known else "server"),
        ("trend", ctx["ema_trend"] or "?"),
        ("ema_fast", px(ctx["ema_fast"])),
        ("ema_slow", px(ctx["ema_slow"])),
        ("ema_fast_period", str(CONTEXT_EMA_FAST)),
        ("ema_slow_period", str(CONTEXT_EMA_SLOW)),
        ("ema_sep_pips", num(sep)),
        # "Fast above slow" is the reading the trend families test, so spell the sign out.
        ("ema_sep_side", "above" if (sep or 0.0) >= 0 else "below"),
        ("sma_fast", px(ctx["sma_fast"])),
        ("sma_slow", px(ctx["sma_slow"])),
        ("sma_fast_period", str(CONTEXT_SMA_FAST)),
        ("sma_slow_period", str(CONTEXT_SMA_SLOW)),
        ("sma_sep_pips", num(ctx["sma_sep_pips"])),
        ("atr_pips", num(ctx["atr_pips"])),
        ("atr_period", str(CONTEXT_ATR_PERIOD)),
        ("rsi", num(ctx["rsi"])),
        ("rsi_period", str(ctx["rsi_period"])),
        ("range_bars", str(ctx["range_bars"])),
        ("range_low", px(ctx["range_low"])),
        ("range_high", px(ctx["range_high"])),
        ("range_pos_pct", "?" if ctx["range_pos"] is None else f"{ctx['range_pos'] * 100:.0f}"),
        ("fresh_high", yes(ctx["fresh_high"])),
        ("fresh_low", yes(ctx["fresh_low"])),
        ("pip", f"{pip:.5f}".rstrip("0")),
        ("bars", str(bars.n)),
        ("windows", "/".join(str(look) for look in CONTEXT_CHANGES)),
        *[(f"chg_{look}", signed(ctx["change_pips"][str(look)])) for look in CONTEXT_CHANGES],
    ]


def write_lines(rows: list[dict[str, str]], path: Path) -> None:
    """Write the average series; blank cells stay blank so the chart skips that stretch."""
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO(newline="")
    buffer.write(_rows_stamp(len(rows)))
    writer = csv.DictWriter(buffer, fieldnames=list(LINES_HEADER), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    _write_text_atomically(path, buffer.getvalue())


def write_context(rows: list[tuple[str, str]], path: Path) -> None:
    """Write the panel as `key,value`: read by eye, and looked up by key in MQL5."""
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO(newline="")
    buffer.write(_rows_stamp(len(rows)))
    writer = csv.writer(buffer)
    writer.writerow(["key", "value"])
    for key, value in rows:
        writer.writerow([key, value])
    _write_text_atomically(path, buffer.getvalue())


def chart_path(
    mt5: ReadOnlyMT5, timeframe: str, out: str | None = None, symbol: str = ""
) -> Path | None:
    """Where the chart's copy of the trades lives, or None when there is nowhere to put it.

    Kept separate from the writing so the watcher can ask "is it due?" without loading history:
    the answer depends only on the path and its timestamp. `symbol` is required in practice -
    the watcher, the CLI and the indicator all derive the name per symbol - and the empty
    default only names the shape callers must convert from.
    """
    if out:
        return Path(out) / filename(timeframe, symbol)
    data_path = mt5.terminal_data_path()
    return None if data_path is None else Path(data_path) / ML_DIR / filename(timeframe, symbol)


def export(
    mt5: ReadOnlyMT5,
    bars: Bars,
    symbol: str,
    timeframe: str,
    per_family: int = DEFAULT_PER_FAMILY,
    max_hold_bars: int = DEFAULT_MAX_HOLD,
    out: str | None = None,
    offset_s: int | None = None,
    line_bars: int = DEFAULT_LINE_BARS,
) -> Path | None:  # symbol already names the files: the chart reads them off its own chart
    """Write everything the chart reads to the chart folder. None when there is nowhere to write.

    The trades, the average lines and the backdrop numbers are written together from these one
    bars: the arrow, the line it crossed and the number in the panel are then the same instant of
    the same history. A caller that writes only some of them would be offering the chart a key to
    a lock it cannot see.

    Takes the bars rather than loading them, so a caller that has already read history (the
    watcher, which builds the panel from the same bars) does not scan it twice and cannot end up
    with a chart that disagrees with the panel beside it.
    """
    target = chart_path(mt5, timeframe, out, symbol)
    if target is None:
        return None
    write_csv(build_rows(bars, timeframe, per_family, max_hold_bars), target)
    siblings = _sibling_paths(mt5, timeframe, out, symbol)
    write_lines(build_lines(bars, line_bars), siblings[0])
    write_context(build_context(bars, timeframe, symbol, offset_s), siblings[1])
    return target


def _cli_offset_s(mt5: ReadOnlyMT5, settings: Settings, symbol: str) -> int | None:
    """The broker offset for the panel's clock stamp: configured, else from a live tick.

    None when neither is available - the export then stamps the broker's own clock instead of
    guessing one, the same way `watch._broker_offset_s` leaves the live panel unstamped.
    """
    if settings.utc_offset_hours is not None:
        return int(settings.utc_offset_hours * 3600)
    try:
        tick = mt5.symbol_info_tick(symbol)
    except Exception:  # noqa: BLE001 - an unstamped clock is not worth failing an export over
        return None
    if tick is None:
        return None
    offset = estimate_server_offset_s(int(tick.time), int(time.time()))
    return offset if offset_is_plausible(offset) else None


def _sibling_paths(
    mt5: ReadOnlyMT5, timeframe: str, out: str | None, symbol: str
) -> tuple[Path, Path]:
    """The lines and context files that sit beside the trades file, in the same directory."""
    trades = chart_path(mt5, timeframe, out, symbol)
    assert trades is not None, "callers check chart_path before asking for its siblings"
    folder = trades.parent
    return (
        folder / lines_filename(timeframe, symbol),
        folder / context_filename(timeframe, symbol),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Export taken trades for the MT5 chart indicator")
    parser.add_argument("timeframe", nargs="?", default="H1", choices=sorted(TIMEFRAMES))
    parser.add_argument("--bars", type=int, default=DEFAULT_BARS)
    parser.add_argument("--per-family", type=int, default=DEFAULT_PER_FAMILY)
    parser.add_argument("--max-hold", type=int, default=DEFAULT_MAX_HOLD)
    parser.add_argument("--out", default=None, help="directory to write into (default: MQL5\\Files)")
    parser.add_argument(
        "--line-bars", type=int, default=DEFAULT_LINE_BARS, help="bars of EMA/SMA to export"
    )
    parser.add_argument(
        "--symbol",
        default="",
        help="symbol to export (default: SYMBOL_ROOT from .env, e.g. USDJPY)",
    )
    args = parser.parse_args()

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

    mt5 = ReadOnlyMT5(backend)
    mt5.connect(
        login=settings.login,
        password=settings.password,
        server=settings.server,
        terminal_path=settings.terminal_path,
    )
    try:
        symbol = args.symbol.strip().upper() or settings.symbol_root
        if chart_path(mt5, args.timeframe, args.out, symbol) is None:
            print("Could not read the terminal's data path; pass --out <dir> instead.")
            return 2
        bars = load_bars(
            mt5,
            symbol,
            args.timeframe,
            args.bars,
            utc_offset_s=_cli_offset_s(mt5, settings, symbol),
        )
        if bars is None:
            print("Insufficient history from terminal.")
            return 2
        path = export(
            mt5,
            bars,
            symbol,
            args.timeframe,
            per_family=args.per_family,
            max_hold_bars=args.max_hold,
            out=args.out,
            offset_s=_cli_offset_s(mt5, settings, symbol),
            line_bars=args.line_bars,
        )
    finally:
        mt5.shutdown()
    if path is None:
        print("Could not read the terminal's data path; pass --out <dir> instead.")
        return 2

    rows = build_rows(bars, args.timeframe, args.per_family, args.max_hold)
    open_rows = [row for row in rows if row["state"] == "open"]
    lines_rows = build_lines(bars, args.line_bars)
    print(f"{symbol} {args.timeframe}: wrote {len(rows)} trades to {path}")
    print(f"  {len(open_rows)} of them still running: " + ", ".join(sorted({r["family"] for r in open_rows})))
    print(f"  {len(lines_rows)} bars of EMA/SMA to {lines_filename(args.timeframe, symbol)}")
    print(f"  backdrop numbers to {context_filename(args.timeframe, symbol)}")
    print(f"  bars {bars.n}, up to {_stamp(int(bars.time[-1]))} (broker clock)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
