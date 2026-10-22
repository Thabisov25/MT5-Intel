"""Iteration loop CLI: python -m mt5_intel.research.iterate

Runs the parameter sweep on a train window, grades the train-best candidate on a
disjoint test window (placebo + cost stress gates), then re-runs the replay at the
chosen as-of time with only cards from validated candidates. Prints and journals
the verdict for the loop: fix rules, rerun, check, repeat.
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
from .backtest import TIMEFRAMES, load_bars, resolve_offset_s
from .replay import _maybe_epoch, _utc, build_cards, summarize_cards
from .strategies import FAMILIES
from .strategies_scalp import MIN_STOP_SPREAD_MULT, SCALP_FAMILIES, cost_hurdle
from .sweep import sweep_family

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REPORT_DIR = PROJECT_ROOT / "reports"
DEFAULT_TEST_FRACTION = 0.3  # trailing fraction of the loaded window is out-of-sample
SCALP_IDS = frozenset(f["id"] for f in SCALP_FAMILIES)


def _fmt_r(value: float | None) -> str:
    return "n/a" if value is None else format(value, "+.3f")


def _write_report(report: dict[str, Any], stamp: datetime) -> Path:
    """Persist the sweep + out-of-sample findings so the dashboard can show them."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    base = REPORT_DIR / f"research-iterate-{stamp.strftime('%Y%m%d-%H%M%S')}"
    (base.with_suffix(".json")).write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = [
        "# Iteration run (sweep on train, graded out-of-sample)",
        "",
        f"- Generated: {report['stamp']} (UTC)",
        (
            f"- Timeframe: {report['timeframe']} | bars: {report['bar_count']} "
            f"({report['first_utc']} to {report['last_utc']})"
        ),
        (
            f"- Train: [0, {report['train_end']}) | Test: the remaining "
            f"{report['test_fraction']:.0%} | max hold {report['max_hold_bars']} bars"
        ),
        (
            f"- Cost rule: stop >= {report['cost']['min_stop_spread_mult']:g} x spread "
            f"= {report['cost']['floor_pips']} pips at a median spread of "
            f"{report['cost']['spread_pips_median']} pips, i.e. round-trip cost "
            f"<= {report['cost']['round_trip_cost_share_of_risk']:.0%} of risk"
        ),
        "- Gates on test: mean R > 0, n >= 30, beats the side-flip placebo, survives 1.5x costs",
        "- Gross = the same trades with zero fill cost: it separates \"no edge\" from \"eaten by costs\"",
        "",
        "| Family | Best params | Train | Test | Gross test | Placebo | 1.5x costs | Cost hurdle | Verdict |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in report["families"]:
        hurdle = row.get("hurdle")
        hurdle_text = "-"
        if hurdle is not None:
            share = hurdle["kept_share"]
            cost_share = hurdle["cost_share_of_median_stop"]
            hurdle_text = (
                f"{hurdle['raw_signals']}->{hurdle['kept_signals']} "
                f"({'n/a' if share is None else format(share, '.0%')}); "
                f"stop {hurdle['median_stop_pips']} vs {hurdle['floor_pips']} pips; "
                f"cost {'n/a' if cost_share is None else format(cost_share, '.0%')} of stop"
            )
        lines.append(
            f"| {row['family']} | {row['best_train']} | {_fmt_r(row['train_mean'])} "
            f"(n={row['train_n']}) | {_fmt_r(row['test_mean'])} (n={row['test_n']}) | "
            f"{_fmt_r(row.get('gross_test_mean'))} | "
            f"{_fmt_r(row['placebo_mean'])} | {_fmt_r(row['stressed_mean'])} | "
            f"{hurdle_text} | {'VALIDATED' if row['validated'] else 'rejected'} |"
        )
    lines += ["", f"**Verdict**: {report['verdict']}"]
    if report.get("validated_only", {}).get("n"):
        lines.append(
            f"Validated-only replay: {report['validated_only']['n']} cards, "
            f"mean {_fmt_r(report['validated_only']['net_r_mean'])}R"
        )
    (base.with_suffix(".md")).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report: {base.with_suffix('.md')}")
    return base


def run(
    timeframe: str = "H1",
    limit: int | None = None,
    asof: str | None = None,
    max_hold_bars: int = 120,
    test_fraction: float = DEFAULT_TEST_FRACTION,
    cost_mult: float = MIN_STOP_SPREAD_MULT,
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
    validated_ids: set[str] = set()
    stamp = datetime.now(UTC)
    report: dict[str, Any] = {
        "stamp": stamp.isoformat(timespec="seconds"),
        "kernel": "iterate",
        "families": [],
    }
    try:
        bars = load_bars(
            mt5,
            settings.symbol_root,
            timeframe,
            limit or 80_000,
            utc_offset_s=resolve_offset_s(mt5, settings, settings.symbol_root),
        )
        if bars is None:
            print("Insufficient history from terminal.")
            return 2
        n = bars.n
        train_end = int(n * (1 - test_fraction))
        # Scalping templates apply on intraday timeframes only (5m-1h bars).
        scalping = timeframe in ("M5", "M15", "M30", "H1")
        families = [*FAMILIES, *SCALP_FAMILIES] if scalping else FAMILIES
        print(
            f"{timeframe}: {n} bars | train [0, {train_end}) | "
            f"test [{train_end}, {n}) | test_fraction={test_fraction:g}"
            + (" | scalping families included" if scalping else "")
        )
        print(
            f"cost rule: stop >= {cost_mult:g} x spread "
            f"(round-trip cost <= {2 / cost_mult:.0%} of risk) | "
            f"median spread {np.median(bars.spread_pips()):.2f} pips | "
            f"floor {cost_mult * np.median(bars.spread_pips()):.1f} pips"
        )
        for family in families:
            cands = sweep_family(
                bars, family["id"], family["fn"], 0, train_end, max_hold_bars, cost_mult
            )
            if not cands:
                print(f"  {family['id']:22s} no trainable candidates")
                continue
            best = cands[0]
            for c in cands:
                if c.train_mean > best.train_mean:
                    best = c
            status = "VALIDATED" if best.validated else "rejected"
            print(
                f"  {family['id']:22s} best-on-train {best.params} "
                f"train={best.train_mean:+.3f}R(n={best.train_n}) "
                f"test={best.test_mean if best.test_mean is None else format(best.test_mean, '+.3f')}R(n={best.test_n}) "
                f"gross-test={best.gross_test_mean if best.gross_test_mean is None else format(best.gross_test_mean, '+.3f')}R "
                f"placebo={best.placebo_mean if best.placebo_mean is None else format(best.placebo_mean, '+.3f')}R "
                f"stress={best.stressed_mean if best.stressed_mean is None else format(best.stressed_mean, '+.3f')}R "
                f"-> {status}"
            )
            hurdle: dict[str, Any] | None = None
            if family["id"] in SCALP_IDS:
                # How much of this template's signal flow the cost rule removes, and
                # where its stops sit relative to the measured spread floor.
                hurdle = cost_hurdle(
                    bars, family["fn"], cost_mult=cost_mult, **best.params
                )
                share = hurdle["kept_share"]
                print(
                    f"      cost hurdle: {hurdle['raw_signals']} raw -> "
                    f"{hurdle['kept_signals']} kept "
                    f"({'n/a' if share is None else format(share, '.0%')}) | "
                    f"median stop {hurdle['median_stop_pips']} pips vs "
                    f"floor {hurdle['floor_pips']} pips "
                    f"(median spread {hurdle['spread_pips_median']} pips)"
                )
            report["families"].append(
                {
                    "family": family["id"],
                    "best_train": best.params,
                    "train_mean": best.train_mean,
                    "train_n": best.train_n,
                    "test_mean": best.test_mean,
                    "test_n": best.test_n,
                    "placebo_mean": best.placebo_mean,
                    "stressed_mean": best.stressed_mean,
                    "gross_train_mean": best.gross_train_mean,
                    "gross_test_mean": best.gross_test_mean,
                    "validated": best.validated,
                    "hurdle": hurdle,
                }
            )
            if best.validated:
                validated_ids.add(family["id"])

        # Replay at the as-of clock with ONLY validated families generating cards.
        asof_epoch = _maybe_epoch(asof)
        cards: list[dict[str, Any]] = []
        for family in families:
            if family["id"] not in validated_ids:
                continue
            cards.extend(build_cards(bars, family, max_hold_bars, asof_epoch=asof_epoch))
        summ_all = summarize_cards(cards, "validated-only")
        print(
            f"  validated-only replay: n={summ_all['n']} "
            + (
                f"mean={summ_all['net_r_mean']:+.3f}R"
                if summ_all["net_r_mean"] is not None
                else "no cards"
            )
        )
        report["asof_epoch"] = asof_epoch
        report["asof_utc"] = _utc(asof_epoch) if asof_epoch else None
        report["validated_only"] = summ_all
        report.update(
            {
                "timeframe": timeframe,
                "bar_count": int(n),
                "first_utc": _utc(int(bars.time[0])),
                "last_utc": _utc(int(bars.time[-1])),
                "train_end": int(train_end),
                "test_fraction": test_fraction,
                "max_hold_bars": max_hold_bars,
                "scalping_included": bool(scalping),
                "cost": {
                    "spread_pips_median": round(float(np.median(bars.spread_pips())), 2),
                    "min_stop_spread_mult": cost_mult,
                    "floor_pips": round(float(cost_mult * np.median(bars.spread_pips())), 2),
                    "round_trip_cost_share_of_risk": round(2 / cost_mult, 3),
                },
                "validated": sorted(validated_ids),
                "verdict": (
                    f"{len(validated_ids)} validated: {sorted(validated_ids)}"
                    if validated_ids
                    else "no family passed all gates on this timeframe"
                ),
            }
        )
        store.append_event(
            "research_iterate",
            {
                "timeframe": timeframe,
                "validated": sorted(validated_ids),
                "n_cards": summ_all["n"],
                "net_r_mean": summ_all["net_r_mean"],
                "stamp": report["stamp"],
            },
        )
        _write_report(report, stamp)
    finally:
        mt5.shutdown()
    print(
        "Verdict: "
        + (
            f"{len(validated_ids)} validated families: {sorted(validated_ids)}"
            if validated_ids
            else "no family passed all three gates — the loop continues, honestly"
        )
    )
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fix-test-fix iteration harness")
    parser.add_argument("timeframe", nargs="?", default="H1", choices=sorted(TIMEFRAMES))
    parser.add_argument("--bars", type=int, default=None)
    parser.add_argument("--asof", default=None)
    parser.add_argument("--max-hold", type=int, default=120)
    parser.add_argument("--test-fraction", type=float, default=DEFAULT_TEST_FRACTION)
    parser.add_argument(
        "--cost-mult",
        type=float,
        default=MIN_STOP_SPREAD_MULT,
        help="minimum stop as a multiple of the quoted spread (round-trip cost = 2/x of risk)",
    )
    args = parser.parse_args()
    raise SystemExit(
        run(
            args.timeframe,
            args.bars,
            args.asof,
            args.max_hold,
            args.test_fraction,
            args.cost_mult,
        )
    )
