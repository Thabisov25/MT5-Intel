"""Run one family's freqtrade backtest end-to-end, for the dashboard to spawn.

This is the single entry point behind the Research page's Freqtrade panel. It runs in the
**project** venv (it needs the same .env the watcher reads), and drives the freqtrade venv
through subprocesses so the two dependency worlds never touch:

1. bars: `tools/freqtrade_export.py` re-exports from MT5 when the CSVs are missing or older
   than --max-age-hours (the terminal must be running - it is, whenever the watcher is).
2. conversion: `tools/freqtrade_sync.py` in the freqtrade venv.
3. `freqtrade backtesting` in the freqtrade venv. The strategy's best hyperopt parameters
   apply automatically when `user_data/strategies/MT5Family.json` names this strategy, and the
   live watcher's stop management is passed in through the environment so the backtest grades
   the system that actually trades.
4. the newest result zip is parsed and `dashboard/freqtrade-results.json` is updated with
   one entry per family - the artifact the dashboard reads back.

Usage:

    .venv/Scripts/python.exe tools/freqtrade_run_backtest.py sweep_reclaim
    .venv/Scripts/python.exe tools/freqtrade_run_backtest.py carry_trend --timerange 20260401-

Prints the family's result as JSON on stdout so the API route can return it directly.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mt5_intel.config import load_stop_manage  # noqa: E402
FQ_VENV = PROJECT_ROOT / ".venv-freqtrade"
FQ_PY = FQ_VENV / "Scripts" / "python.exe"
FREQTRADE = FQ_VENV / "Scripts" / "freqtrade.exe"
RESULTS_DIR = PROJECT_ROOT / "user_data" / "backtest_results"
ARTIFACT = PROJECT_ROOT / "dashboard" / "freqtrade-results.json"
CSV_DIR = PROJECT_ROOT / "user_data" / "mt5_ohlcv"
PARAMS_FILE = PROJECT_ROOT / "user_data" / "strategies" / "MT5Family.json"
ARCHIVE = PROJECT_ROOT / "user_data" / "hyperopt_results" / "archive"

# The families the strategy file exposes, keyed by the registry's own ids - the same ids the
# dashboard and the research tables use, so one name works everywhere.
STRATEGIES = {
    "sweep_reclaim": "SweepReclaimFT",
    "carry_trend": "CarryTrendFT",
    "compression_breakout": "CompressionBreakoutFT",
    "trend_pullback": "TrendPullbackFT",
    "range_extreme_mr": "RangeExtremeFT",
    "daily_momentum": "DailyMomentumFT",
    "ma_crossover": "MaCrossoverFT",
    "rsi_mean_reversion": "RsiMeanReversionFT",
}
DEFAULT_TIMERANGE = "20250101-"


def _run(cmd: list[str], *, timeout: int, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = None
    if extra_env:
        env = {**os.environ, **extra_env}
    return subprocess.run(
        [str(c) for c in cmd],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
        env=env,
    )


def manage_env() -> dict[str, str]:
    """The live watcher's stop-management settings, handed to the freqtrade venv.

    The strategy file runs in an environment that has no project dependencies and no broker
    credentials, so it cannot read the .env for itself - but a backtest that left the watcher's
    stop management out would be grading a system that does not trade. `load_stop_manage` is the
    same reader `load_settings` uses, so one line in the .env moves the live watcher and this
    engine together and the two can never disagree about where a stop went.
    """
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    stop = load_stop_manage()
    return {
        "MT5INTEL_MANAGE": "1" if stop.manage else "0",
        "MT5INTEL_BREAKEVEN_R": str(stop.breakeven_r),
        "MT5INTEL_LOCK_R": str(stop.lock_r),
        "MT5INTEL_TRAIL_R": str(stop.trail_r),
        "MT5INTEL_TRAIL_DISTANCE_R": str(stop.trail_distance_r),
    }


def bars_fresh(max_age_hours: float) -> bool:
    if not CSV_DIR.exists():
        return False
    csvs = list(CSV_DIR.glob("*_H1.csv"))
    if not csvs:
        return False
    youngest = max(p.stat().st_mtime for p in csvs)
    return (time.time() - youngest) < max_age_hours * 3600


def ensure_bars(max_age_hours: float, refresh: bool) -> None:
    if not refresh and bars_fresh(max_age_hours):
        return
    proc = _run([sys.executable, PROJECT_ROOT / "tools" / "freqtrade_export.py"], timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"bar export failed: {proc.stderr[-500:]}")


def stage_params(family: str) -> None:
    """Put THIS family's hyperopted params where freqtrade will read them, before it runs.

    Every strategy class lives in one module (MT5Family.py), so freqtrade reads one params
    file (MT5Family.json) for all of them - and its loader refuses the file outright unless
    its strategy_name matches the class being run. Left over from a sweep of another family,
    that mismatch made every run button except the last sweep's winner fail with "Invalid
    parameter file provided"; a family with no hyperopt of its own could never run at all.

    The sweep already archives each family's params under its own name; this mirrors that
    convention from the runner's side: the current file is archived under the family it
    belongs to (never lost), then the requested family's params are copied in place - or the
    file is left absent, which is freqtrade's own "run the strategy's recorded defaults"
    path. `parse_result` reads the same file afterwards, so the panel always shows the
    parameters that were actually in force for its row.
    """
    by_strategy = {strategy: fam for fam, strategy in STRATEGIES.items()}
    if PARAMS_FILE.exists():
        try:
            current = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
            owner = by_strategy.get(str(current.get("strategy_name")))
        except (OSError, ValueError):
            owner = None
        if owner is None:
            # Unreadable or unnameable: quarantine rather than delete, so a hand-written
            # params file is recoverable instead of silently destroyed.
            ARCHIVE.mkdir(parents=True, exist_ok=True)
            PARAMS_FILE.replace(ARCHIVE / f"MT5Family_stray_{int(time.time())}.json")
        elif owner == family:
            return  # already the right file; a second run keeps its own params
        else:
            ARCHIVE.mkdir(parents=True, exist_ok=True)
            shutil.move(str(PARAMS_FILE), ARCHIVE / f"MT5Family_{owner}.json")
    archived = ARCHIVE / f"MT5Family_{family}.json"
    if archived.exists():
        PARAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(str(archived), PARAMS_FILE)


def ensure_converted() -> None:
    proc = _run([FQ_PY, PROJECT_ROOT / "tools" / "freqtrade_sync.py"], timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"conversion failed: {proc.stderr[-500:]}")


def backtest(strategy: str, timerange: str) -> dict:
    newest_before = max(RESULTS_DIR.glob("*.zip"), key=lambda p: p.stat().st_mtime, default=None)
    proc = _run(
        [FREQTRADE, "backtesting", "--config", "user_data/ft-config.json",
         "--strategy", strategy, "--timerange", timerange],
        timeout=600,
        extra_env=manage_env(),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"backtest failed: {(proc.stderr or proc.stdout)[-500:]}")
    zips = sorted(RESULTS_DIR.glob("*.zip"), key=lambda p: p.stat().st_mtime)
    result_zip = zips[-1] if zips and zips[-1] != newest_before else (zips[-1] if zips else None)
    if result_zip is None:
        raise RuntimeError("backtest produced no result file")
    return parse_result(result_zip, strategy)


def parse_result(result_zip: Path, strategy: str) -> dict:
    """Metrics from a result zip, including expectancy in R (profit divided by each
    trade's own stop distance - the number the hyperopt loss scores, kept identical so
    the panel and the search never disagree about what expectancy means)."""
    with zipfile.ZipFile(result_zip) as handle:
        main = [n for n in handle.namelist() if n.endswith(".json") and "config" not in n][0]
        payload = json.loads(handle.read(main))
    strat = payload["strategy"][strategy]
    trades = strat.get("trades", [])
    wins = losses = 0
    r_sum_win = r_sum_loss = 0.0
    for row in trades:
        stop = abs(float(row.get("stop_loss_ratio") or 0.0))
        ratio = float(row.get("profit_ratio") or 0.0)
        if stop <= 1e-6:
            continue  # a trade without a usable stop distance cannot be scored in R
        r = ratio / stop
        if r > 0:
            wins += 1
            r_sum_win += r
        elif r < 0:
            losses += 1
            r_sum_loss += -r
    decided = wins + losses
    expectancy = (
        (wins / decided) * (r_sum_win / wins if wins else 0.0)
        - (losses / decided) * (r_sum_loss / losses if losses else 0.0)
        if decided
        else None
    )
    # The parameters actually in force for this run, when the hyperopt file names this
    # strategy. Read rather than assumed: the panel shows what was measured, not what a
    # defaults table says should have been.
    params_path = PROJECT_ROOT / "user_data" / "strategies" / "MT5Family.json"
    applied: dict | None = None
    if params_path.exists():
        try:
            saved = json.loads(params_path.read_text(encoding="utf-8"))
            if saved.get("strategy_name") == strategy:
                applied = saved.get("params", {}).get("buy")
        except (OSError, ValueError):
            applied = None
    perf = strat  # the summary fields live flat on the strategy block in this version
    return {
        "strategy": strategy,
        "timerange": str(strat.get("timerange") or ""),
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / decided, 4) if decided else None,
        "expectancy_r": round(expectancy, 4) if expectancy is not None else None,
        "net_profit_pct": round(float(strat.get("profit_total") or 0.0) * 100, 3),
        "max_drawdown_pct": round(float(strat.get("max_relative_drawdown") or 0.0) * 100, 2),
        "buy_params": applied,
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def update_artifact(family: str, entry: dict) -> dict:
    artifact: dict = {"runs": {}}
    if ARTIFACT.exists():
        try:
            artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            artifact = {"runs": {}}
    artifact.setdefault("runs", {})[family] = entry
    artifact["generated_utc"] = datetime.now(UTC).isoformat(timespec="seconds")
    tmp = ARTIFACT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    tmp.replace(ARTIFACT)
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("family", choices=sorted(STRATEGIES))
    parser.add_argument("--timerange", default=DEFAULT_TIMERANGE)
    parser.add_argument("--max-age-hours", type=float, default=6.0)
    parser.add_argument("--refresh", action="store_true", help="re-export bars even when fresh")
    args = parser.parse_args()

    strategy = STRATEGIES[args.family]
    ensure_bars(args.max_age_hours, args.refresh)
    ensure_converted()
    stage_params(args.family)
    entry = backtest(strategy, args.timerange)
    update_artifact(args.family, entry)
    print(json.dumps(entry))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
