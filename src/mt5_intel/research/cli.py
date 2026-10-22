"""Research CLI: python -m mt5_intel.research.run

Evaluates every family against placebo controls and cost stress on real history,
writes a Markdown + JSON trial report, and records per-family status in the store.
Trial log entries count toward future deflated Sharpe (D-026): every run is recorded,
including failures.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import ConfigError, load_settings
from ..mt5_adapter import ReadOnlyMT5
from ..store import Store
from .backtest import load_bars, resolve_offset_s
from .strategies import FAMILIES
from .validate import evaluate_family

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REPORT_DIR = PROJECT_ROOT / "reports"

# Promotion requires surviving 1.5x costs (blueprint step 3). `evaluate_family` multiplies the
# spreads by this factor, and it defaults to 1.0, so it has to be passed explicitly: the trial
# command previously left it at the default and reported an unstressed column as "Stressed 1.5x".
COST_STRESS = 1.5


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
    mt5 = ReadOnlyMT5(backend)
    mt5.connect(
        login=settings.login,
        password=settings.password,
        server=settings.server,
        terminal_path=settings.terminal_path,
    )
    store = Store(PROJECT_ROOT / "data" / "mt5.db")
    stamp = datetime.now(UTC)
    results: list[dict[str, Any]] = []
    try:
        for timeframe in ("M15", "H1"):
            bars = load_bars(
                mt5,
                settings.symbol_root,
                timeframe,
                80_000,
                utc_offset_s=resolve_offset_s(mt5, settings, settings.symbol_root),
            )
            if bars is None:
                print(f"{timeframe}: insufficient history, skipped")
                continue
            days = (int(bars.time[-1]) - int(bars.time[0])) / 86_400
            print(f"{timeframe}: {bars.n} bars, {days:.0f} days")
            for family in FAMILIES:
                t0 = time.perf_counter()
                res = evaluate_family(bars, family, spread_multiplier=COST_STRESS)
                res["timeframe"] = timeframe
                res["days"] = round(days, 1)
                res["elapsed_s"] = round(time.perf_counter() - t0, 1)
                results.append(res)
                status = res["status"]
                print(
                    f"  {family['id']:22s} n={res['full']['n']:4d} "
                    f"mean={res['full']['net_r_mean']:+.3f}R edge_vs_placebo={res['edge_vs_placebo']:+.3f}R "
                    f"stressed={res['stressed']['net_r_mean'] if res['stressed']['net_r_mean'] is not None else 'n/a':+} "
                    f"folds+{res['folds_positive_share']:.0%} -> {status}"
                )
    finally:
        mt5.shutdown()

    payload = {
        "generated_utc": stamp.isoformat(timespec="seconds"),
        "results": results,
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    base = REPORT_DIR / f"research-{stamp.strftime('%Y%m%d-%H%M%S')}"
    (base.with_suffix(".json")).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Research trial log",
        "",
        f"- Generated: {stamp.isoformat(timespec='seconds')} (UTC)",
        "- Costs: measured M5 bar snapshot spread, charged twice (entry + exit), in R",
        "- Placebo: side-flipped control, identical timing and stops (design doc D-008)",
        "- Promotion requires: OOS mean > 0, lower 90% CI > 0, beats placebo, 1.5x costs > 0, >= 60% folds positive, n >= 30",
        "",
        "| Timeframe | Family | n | Mean (R) | Edge vs placebo (R) | Stressed 1.5x (R) | Folds+ | Lower CI (R) | Status |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        stressed = r["stressed"]["net_r_mean"]
        lines.append(
            f"| {r['timeframe']} | {r['family']} | {r['full']['n']} | "
            f"{r['full']['net_r_mean']:+.3f} | {r['edge_vs_placebo']:+.3f} | "
            f"{'n/a' if stressed is None else format(stressed, '+.3f')} | "
            f"{r['folds_positive_share']:.0%} | "
            f"{'n/a' if r['lower_ci_r'] is None else format(r['lower_ci_r'], '+.3f')} | {r['status']} |"
        )
    (base.with_suffix(".md")).write_text("\n".join(lines) + "\n", encoding="utf-8")

    store.append_event(
        "research_trial",
        {
            "stamp": stamp.isoformat(timespec="seconds"),
            "n_results": len(results),
            "promoted": [r["family"] for r in results if r["promoted"]],
        },
    )
    # The gate reads this registry: only 'historically validated' families enable card generation.
    for r in results:
        store.set_strategy_status(
            family=r["family"],
            timeframe=r["timeframe"],
            status=r["status"],
            evidence={
                "n": r["full"]["n"],
                "mean_r": r["full"]["net_r_mean"],
                "edge_vs_placebo_r": r["edge_vs_placebo"],
                "stressed_mean_r": r["stressed"]["net_r_mean"],
                "folds_positive_share": r["folds_positive_share"],
                "lower_ci_r": r["lower_ci_r"],
                "report": base.with_suffix(".md").name,
            },
        )
    promoted = [r["family"] for r in results if r["promoted"]]
    print(f"Promoted: {promoted if promoted else 'none — gate stays default-deny (honest result)'}")
    print(f"Report: {base.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    run()
