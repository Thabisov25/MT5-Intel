"""The trust search: hyperopt every family, then grade the winner out-of-sample.

One run per family, sequentially (freqtrade's hyperopt is itself multi-process; running
families in parallel would fight over the CPU and the one params file):

1. `freqtrade hyperopt` on the *training* window (default 20250101-20260401), scored by
   ExpectancyRHyperOptLoss - expectancy in R, each trade divided by its own stop distance.
   The buy space includes the two shared knobs (`tp_r`, `stop_scale`), so the win-rate vs
   expectancy trade is a searched dimension rather than an assumption. The live watcher's stop
   management is in force throughout, passed in from the same .env the watcher reads, so the
   search optimises the system that actually trades.
2. A backtest with the best params on the training window (the in-sample record).
3. A backtest on the *held-out* window (default 20260401- to now) that hyperopt never saw.
   Only this number is allowed to call a family trustworthy.
4. The params file is archived per family so the next family starts clean; at the end, the
   best out-of-sample family's params are restored so the dashboard's run buttons measure
   the champion.

Everything is appended to `dashboard/freqtrade-results.json` under a `sweep` key, next to
the per-family runs the Research page's buttons write - the panel and this search share
one artifact and one definition of expectancy.

Run in the project venv, ideally in the background (it takes roughly an hour):

    .venv/Scripts/python.exe tools/freqtrade_sweep.py --epochs 30
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

from freqtrade_run_backtest import (
    ARCHIVE,
    ARTIFACT,
    FQ_PY,
    FREQTRADE,
    PARAMS_FILE,
    PROJECT_ROOT,
    STRATEGIES,
    backtest,
    ensure_bars,
    ensure_converted,
    manage_env,
)

LOSS = "ExpectancyRHyperOptLoss"


def _run(cmd: list[str], *, timeout: int, log: Path | None = None) -> None:
    """Run a freqtrade command, streaming to a log file when given one.

    hyperopt is the long pole and its progress lines are the only sign of life, so the
    sweep writes each command to its own log under logs/ rather than swallowing output.
    """
    import subprocess

    if log is None:
        proc = subprocess.run([str(c) for c in cmd], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=timeout, env=manage_env())
        if proc.returncode != 0:
            raise RuntimeError(f"{cmd[1] if len(cmd) > 1 else cmd} failed: {(proc.stderr or proc.stdout)[-400:]}")
        return
    with log.open("w", encoding="utf-8") as handle:
        proc = subprocess.run([str(c) for c in cmd], cwd=PROJECT_ROOT, stdout=handle, stderr=subprocess.STDOUT, timeout=timeout, env=manage_env())
    if proc.returncode != 0:
        tail = log.read_text(encoding="utf-8", errors="replace")[-400:]
        raise RuntimeError(f"{cmd[1]} failed: {tail}")


def archive_params(family: str) -> None:
    if PARAMS_FILE.exists():
        ARCHIVE.mkdir(parents=True, exist_ok=True)
        shutil.move(str(PARAMS_FILE), ARCHIVE / f"MT5Family_{family}.json")


def hyperopt(family: str, strategy: str, train_range: str, epochs: int, seed: int) -> Path:
    log = PROJECT_ROOT / "logs" / f"hyperopt_{family}.log"
    _run(
        [FREQTRADE, "hyperopt", "--config", "user_data/ft-config.json", "--strategy", strategy,
         "--hyperopt-loss", LOSS, "--spaces", "buy", "--epochs", str(epochs),
         "--timerange", train_range, "--random-state", str(seed)],
        timeout=3 * 3600,
        log=log,
    )
    return log


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epochs", type=int, default=30, help="hyperopt epochs per family")
    parser.add_argument("--train", default="20250101-20260401", help="training window")
    parser.add_argument("--holdout", default="20260401-", help="held-out window")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--families", nargs="*", default=sorted(STRATEGIES), choices=sorted(STRATEGIES))
    args = parser.parse_args()

    ensure_bars(max_age_hours=12.0, refresh=False)
    ensure_converted()
    ARCHIVE.mkdir(parents=True, exist_ok=True)

    summary: list[dict] = []
    started = time.time()
    for index, family in enumerate(args.families):
        strategy = STRATEGIES[family]
        family_started = time.time()
        print(f"[{index + 1}/{len(args.families)}] {family}: hyperopt {args.epochs} epochs...", flush=True)
        archive_params(family)  # start clean: the params file must not leak between families
        try:
            hyperopt(family, strategy, args.train, args.epochs, args.seed)
            in_sample = backtest(strategy, args.train)
            holdout = backtest(strategy, args.holdout)
            entry = {
                "family": family,
                "strategy": strategy,
                "in_sample": in_sample,
                "holdout": holdout,
                "minutes": round((time.time() - family_started) / 60, 1),
                "ok": True,
            }
        except Exception as exc:  # noqa: BLE001 - one broken family must not end the sweep
            entry = {"family": family, "strategy": strategy, "ok": False, "error": str(exc)[:300]}
        summary.append(entry)
        archive_params(family)
        _write_artifact(summary, args)
        verdict = (
            f"{entry['holdout']['trades']} OOS trades, expectancy {entry['holdout']['expectancy_r']}R, "
            f"win {entry['holdout']['win_rate']}"
            if entry.get("ok")
            else f"FAILED: {entry.get('error', '')[:80]}"
        )
        print(f"[{index + 1}/{len(args.families)}] {family}: {verdict} ({entry.get('minutes', '?')} min)", flush=True)

    # Champion's params back where the dashboard's run buttons will find them.
    credible = [e for e in summary if e.get("ok") and (e["holdout"].get("expectancy_r") is not None)]
    if credible:
        best = max(credible, key=lambda e: e["holdout"]["expectancy_r"])
        champion = ARCHIVE / f"MT5Family_{best['family']}.json"
        if champion.exists():
            shutil.copy(str(champion), PARAMS_FILE)
        print(f"\nBest out-of-sample: {best['family']} ({best['holdout']['expectancy_r']}R, "
              f"{best['holdout']['win_rate']} win over {best['holdout']['trades']} trades)", flush=True)
    print(f"total {round((time.time() - started) / 60, 1)} min", flush=True)
    return 0


def _write_artifact(summary: list[dict], args: argparse.Namespace) -> None:
    artifact: dict = {"runs": {}}
    if ARTIFACT.exists():
        try:
            artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            artifact = {"runs": {}}
    artifact["sweep"] = {
        "train": args.train,
        "holdout": args.holdout,
        "epochs": args.epochs,
        "families": summary,
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    tmp = ARTIFACT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    tmp.replace(ARTIFACT)


if __name__ == "__main__":
    raise SystemExit(main())
