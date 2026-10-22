"""Historical replay: 'what cards would the gate have issued on old markets?'

Reads real history from the terminal, generates the exact card the recommendation
gate would have produced per signal (side, entry, SL, TP, evidence quality), and
grades every card against what actually happened next. Skip comparison means the
system's no-action default is scored too: 'how much money did refusing to act save
or cost?' Never places orders; never writes to the live journal's recommendations
table (a separate kind keeps the replays out of the live card flow).

Usage: python -m mt5_intel.research.replay [timeframe] [limit] [max_hold]
  e.g. python -m mt5_intel.research.replay H1 500 120
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from ..config import ConfigError, load_settings
from ..mt5_adapter import ReadOnlyMT5
from ..store import Store
from .backtest import TIMEFRAMES, one_at_a_time, plan_trade, resolve_offset_s, resolve_outcome
from .strategies import FAMILIES, Family
from .strategies_scalp import SCALP_FAMILIES, SessionClockError

# Scalping templates are intraday-only by construction (session windows, M5-scale stops).
SCALP_TIMEFRAMES = ("M5", "M15", "M30", "H1")


def families_for(timeframe: str) -> list[Family]:
    """Families replayed on a timeframe: intraday adds the scalping templates."""
    return [*FAMILIES, *SCALP_FAMILIES] if timeframe in SCALP_TIMEFRAMES else list(FAMILIES)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REPORT_DIR = PROJECT_ROOT / "reports"
DEFAULT_MAX_HOLD = 120


def _fmt(price: float, digits: int) -> str:
    return format(price, f".{digits}f")


def _utc(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M")


def _title(policy: str) -> str:
    return {"historically validated": "actionable (research-validated)",
            "research candidate": "informational only — research candidate"}.get(policy, policy)


def build_cards(
    bars: Any,
    family: Family,
    max_hold_bars: int = DEFAULT_MAX_HOLD,
    asof_epoch: int | None = None,
) -> list[dict[str, Any]]:
    """Generate replay cards from one family over historical bars."""
    strats = family["fn"](bars)  # a rule that cannot be evaluated raises: the caller reports it
    sig_i, side, stops, tp_r = (np.asarray(a) for a in strats)
    cutoff = runs_at_or_before(bars, asof_epoch)
    # Only the positions the rule could actually be in (D-035): a re-fire while its previous
    # trade is still running is the same trade restated, not a second one to grade.
    taken = one_at_a_time(bars, sig_i, side, stops, tp_r, max_hold_bars)
    cards: list[dict[str, Any]] = []
    for k, _, _ in taken:
        i = int(sig_i[k])
        if i >= cutoff:
            continue  # signal bar is after the replay clock: not knowable then
        sd = int(side[k])
        entry_bar = i + 1
        stop_k = float(stops[k])
        stop = stop_k if stop_k > 0 else None
        tp_r_k = float(tp_r[k])
        plan = (
            plan_trade(bars, i, entry_bar, sd, stop_k, tp_r_k)
            if stop is not None
            else None
        )
        if plan is None:
            continue  # no forward bar to grade against; skip
        outcome = resolve_outcome(bars, plan, i, sd, max_hold_bars)
        if outcome is None:
            continue
        cards.append(
            {
                "family": family["id"],
                "signal_epoch": int(bars.time[i]),
                "signal_utc": _utc(int(bars.time[i])),
                "entry_epoch": int(bars.time[entry_bar]),
                "side": "buy" if sd > 0 else "sell",
                "entry": _fmt(plan["entry"], bars.digits),
                "sl": _fmt(plan["sl"], bars.digits),
                "tp": _fmt(plan["tp"], bars.digits),
                "stop_pips": round(stop_k, 1),
                "tp_r": round(tp_r_k, 1),
                "grade": {
                    "net_r": round(outcome.r, 3),
                    "hit": "TP" if outcome.touched_tp else ("SL" if outcome.touched_sl else "time-exit"),
                    "bars_held": outcome.bars_held,
                },
            }
        )
    return cards


def _maybe_epoch(text: str | None) -> int | None:
    """Parse an as-of time: epoch seconds, ISO date or 'YYYY-MM-DD HH:MM' (UTC)."""
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(text, pattern).replace(tzinfo=UTC).timestamp())
        except ValueError:
            continue
    raise SystemExit(f"Invalid --asof value {text!r}; use epoch seconds or 'YYYY-MM-DD [HH:MM]' (UTC)")


def runs_at_or_before(
    bars: Any,
    asof_epoch: int | None,
) -> int:
    """Replay clock (D-009 in spirit): the as-of bar is the CURRENT closed bar.

    Signals generated only from bars at or before the replay clock; anything after
    that exists solely for grading. Bars.time is epoch seconds (server clock).
    """
    if asof_epoch is None:
        n: int = bars.n
        return n
    cutoff: int = int(np.searchsorted(bars.time, asof_epoch, side="right"))
    return cutoff


def sample_cards(cards: list[dict[str, Any]], per_family: int = 200) -> list[dict[str, Any]]:
    """Cap each family's stored cards so every strategy appears in the report.

    A flat head-of-list cap fills up with whichever family trades most often and hides the
    rest - including the scalping templates, which trade least. Order is preserved.
    """
    seen: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    for card in cards:
        family = str(card["family"])
        taken = seen.get(family, 0)
        if taken >= per_family:
            continue
        seen[family] = taken + 1
        out.append(card)
    return out


def summarize_cards(cards: list[dict[str, Any]], label: str) -> dict[str, Any]:
    if not cards:
        return {"label": label, "n": 0, "net_r_mean": None}
    net = np.array([c["grade"]["net_r"] for c in cards], dtype="float64")
    tp = sum(1 for c in cards if c["grade"]["hit"] == "TP")
    sl = sum(1 for c in cards if c["grade"]["hit"] == "SL")
    return {
        "label": label,
        "n": int(net.size),
        "net_r_mean": round(float(net.mean()), 4),
        "hit_tp": tp,
        "hit_sl": sl,
        "net_sum_r": round(float(net.sum()), 2),
    }


def run(
    timeframe: str = "H1",
    limit: int | None = None,
    max_hold_bars: int = DEFAULT_MAX_HOLD,
    asof: str | None = None,
    sample_every: int = 1,
) -> int:
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
    if timeframe not in TIMEFRAMES:
        print(f"Unknown timeframe {timeframe!r}; expected one of {sorted(TIMEFRAMES)}")
        return 2

    mt5 = ReadOnlyMT5(backend)
    mt5.connect(
        login=settings.login,
        password=settings.password,
        server=settings.server,
        terminal_path=settings.terminal_path,
    )
    store = Store(PROJECT_ROOT / "data" / "mt5.db")
    asof_epoch = _maybe_epoch(asof)
    results: list[dict[str, Any]] = []
    all_cards: list[dict[str, Any]] = []
    try:
        from .backtest import load_bars

        bars = load_bars(
            mt5,
            settings.symbol_root,
            timeframe,
            limit or 80_000,
            utc_offset_s=resolve_offset_s(mt5, settings, settings.symbol_root),
        )
        if bars is None:
            print("Insufficient history from terminal; nothing to replay.")
            return 2
        days = (int(bars.time[-1]) - int(bars.time[0])) / 86_400
        asof_utc = _utc(asof_epoch) if asof_epoch is not None else "latest bar"
        families = families_for(timeframe)
        print(f"{timeframe}: {bars.n} bars, {days:.0f} days, {settings.symbol_root}; replay clock: {asof_utc}")
        print(f"families: {len(families)} ({', '.join(f['id'] for f in families)})")
        for family in families:
            try:
                cards = build_cards(bars, family, max_hold_bars, asof_epoch=asof_epoch)
            except SessionClockError as exc:
                # Loud, and with no cards. A UTC session rule on a clock nobody has read is not
                # a market that went quiet, and reporting n=0 without saying why would be.
                summ = summarize_cards([], family["id"])
                summ["note"] = str(exc)
                results.append(summ)
                print(f"  {family['id']:22s} NOT EVALUATED: {exc}")
                continue
            if sample_every > 1:
                cards = cards[::sample_every]
            summ = summarize_cards(cards, family["id"])
            results.append(summ)
            all_cards.extend(cards)
            grade = f"{summ['net_r_mean']:+.3f}R mean, {summ['hit_tp']} TP / {summ['hit_sl']} SL" if summ["n"] else "no cards"
            print(f"  {family['id']:22s} n={summ['n']:4d}  {grade}")
            # Journal each replay outcome under a 'research_replay' kind so it does not
            # interfere with live gate cards, but is fully auditable.
            store.append_event(
                "research_replay",
                {
                    "family": family["id"],
                    "timeframe": timeframe,
                    "asof_utc": asof_epoch is not None and asof_utc or None,
                    "n_cards": len(cards),
                    "net_r_mean": summ["net_r_mean"],
                    "stamp": datetime.now(UTC).isoformat(timespec="seconds"),
                },
            )
    finally:
        mt5.shutdown()

    stamp = datetime.now(UTC)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    base = REPORT_DIR / f"replay-{stamp.strftime('%Y%m%d-%H%M%S')}"
    payload = {
        "generated_utc": stamp.isoformat(timespec="seconds"),
        "symbol": settings.symbol_root,
        "timeframe": timeframe,
        "max_hold_bars": max_hold_bars,
        "asof_epoch": asof_epoch,
        "asof_utc": asof_epoch is not None and asof_utc or None,
        "families": [f["id"] for f in families],
        "results": results,
        "cards": sample_cards(all_cards),
    }
    base.with_suffix(".json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Historical replay",
        "",
        f"- Generated: {stamp.isoformat(timespec='seconds')} (UTC)",
        f"- Symbol: {settings.symbol_root}  Timeframe: {timeframe}  Replay clock: {asof_utc}",
        f"- Cards generated using only bars at or before the replay clock, graded against what happened next. Max hold {max_hold_bars} bars.",
        "- Conservative fills: entry next bar open (shorts on ask), SL-first on double touch.",
        "",
        "| Family | Cards | Mean (R) | TP hits | SL hits | Total (R) |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        if r["n"] == 0:
            lines.append(f"| {r['label']} | 0 | - | - | - | - |")
        else:
            lines.append(
                f"| {r['label']} | {r['n']} | {r['net_r_mean']:+.3f} | {r['hit_tp']} | {r['hit_sl']} | {r['net_sum_r']:+.1f} |"
            )
    base.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report: {base.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Historical replay with a replay clock")
    parser.add_argument("timeframe", nargs="?", default="H1", choices=sorted(TIMEFRAMES))
    parser.add_argument("--bars", type=int, default=None, help="history length limit")
    parser.add_argument("--max-hold", type=int, default=DEFAULT_MAX_HOLD)
    parser.add_argument("--asof", default=None, help="replay clock: epoch or 'YYYY-MM-DD [HH:MM]' UTC")
    parser.add_argument("--sample-every", type=int, default=1, help="keep every Nth card (for large samples)")
    args = parser.parse_args()
    raise SystemExit(run(args.timeframe, args.bars, args.max_hold, args.asof, args.sample_every))
