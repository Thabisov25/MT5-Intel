"""Regime test: does a rule's edge survive outside the stretch it was tuned on?

Usage: python -m mt5_intel.research.regimes [timeframe] [family]

The `iterate` harness grades one train-best candidate on one trailing test window, so a
"validated" record can rest on a single stretch of market history. This CLI slices the loaded
series into named, disjoint monetary/volatility regimes (the 1970s oil shock when the BoJ
discount rate sat above fed funds, the Volcker tightening, the post-Plaza yen, ZIRP, the 2008
unwind, the 2024 carry unwind) and grades *every* parameter combination in the family's grid
inside each one, with the same placebo and 1.5x-cost gates the promotion rules use.

Reporting the whole grid rather than the train-best is deliberate: the question here is not
"which parameters look good" but "does this rule have an edge in this market condition at all".
A regime counts as carrying the edge only when every parameter combination is positive in it.

The carry sign the rate table assigns inside each window is printed beside the results, because
for `carry_trend` the regime *is* the sign: the same rule asks for opposite sides of USDJPY in
1975 and in 2024.

Nothing here writes to the strategy registry - this is evidence about an existing record, not a
new promotion.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from ..config import ConfigError, load_settings
from ..mt5_adapter import ReadOnlyMT5
from .backtest import TIMEFRAMES, Bars, load_bars, resolve_offset_s, simulate, summarize
from .rates import carry_direction
from .strategies import FAMILIES, Family, matched_placebo
from .strategies_scalp import SCALP_FAMILIES
from .sweep import GRIDS, _family_call, gross_bars

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REPORT_DIR = PROJECT_ROOT / "reports"
MIN_TRADES = 30


@dataclass(frozen=True)
class Regime:
    label: str
    start: str  # inclusive, YYYY-MM-DD
    end: str    # exclusive, YYYY-MM-DD
    note: str


# Disjoint windows covering the D1 history from the first *verified* rate-table row (1973-04)
# through the present. The pre-1973 tail is excluded on purpose: the rate table has no primary
# source there and USDJPY was administratively pegged until the 1971 Smithsonian realignment.
REGIMES: tuple[Regime, ...] = (
    Regime(
        "1973-1976 oil shock / BoJ above Fed",
        "1973-04-01",
        "1977-01-01",
        "BoJ discount rate 5-9% against fed funds 5-10%: carry is negative for most of this window",
    ),
    Regime(
        "1977-1979 Fed tightening returns",
        "1977-01-01",
        "1980-01-01",
        "carry flips back positive as the Fed out-runs the BoJ",
    ),
    Regime(
        "1980-1985 Volcker shock to Plaza",
        "1980-01-01",
        "1985-09-01",
        "the widest positive rate gap of the sample",
    ),
    Regime(
        "1985-1989 post-Plaza yen",
        "1985-09-01",
        "1990-01-01",
        "yen appreciates hard while the BoJ cuts; trend and carry agree against USDJPY",
    ),
    Regime(
        "1990-1995 bubble collapse",
        "1990-01-01",
        "1996-01-01",
        "BoJ cuts toward ZIRP; USDJPY rangebound",
    ),
    Regime(
        "1996-2001 ZIRP and Fed easing", "1996-01-01", "2002-01-01", "carry positive, trend mixed"
    ),
    Regime(
        "2002-2007 carry build-up",
        "2002-01-01",
        "2007-08-01",
        "the textbook carry era: Fed to 5.25%, BoJ 0.5%, low volatility",
    ),
    Regime(
        "2007-2012 GFC and carry unwind",
        "2007-08-01",
        "2013-01-01",
        "the classic carry crash: USDJPY 124 -> 76",
    ),
    Regime(
        "2013-2019 Abenomics and NIRP",
        "2013-01-01",
        "2020-01-01",
        "BoJ negative rate; Fed liftoff 2015-2018",
    ),
    Regime(
        "2020-2021 COVID and reflation", "2020-01-01", "2022-01-01", "policy rates pinned near zero"
    ),
    Regime(
        "2022-2026 hike cycle and 2024 unwind",
        "2022-01-01",
        "2026-10-10",
        "Fed to 5.375% against BoJ 0.25-0.50%; the 2024 yen carry unwind",
    ),
)


def _epoch(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp())


def _bar_bounds(bars: Bars, start: str, end: str) -> tuple[int, int]:
    """Half-open bar-index window [lo, hi) covering [start, end) as UTC dates."""
    return (
        int(np.searchsorted(bars.time, _epoch(start), side="left")),
        int(np.searchsorted(bars.time, _epoch(end), side="left")),
    )


def _carry_signs(bars: Bars, lo: int, hi: int) -> str:
    """The carry directions the rate table assigns inside a window, in order of first use.

    "+" = long USDJPY (positive spread), "-" = short USDJPY (negative spread), "0" = flat.
    Chronological order matters: a window showing "+-" opened with a positive spread and flipped
    negative, i.e. the rule is asked for opposite sides of the same pair inside one regime.
    """
    if hi <= lo:
        return "-"
    seen: list[int] = []
    for i in range(lo, hi):
        direction = carry_direction(int(bars.time[i]))
        if direction not in seen:
            seen.append(direction)
    return "".join("-0+"[s + 1] for s in seen)


def _params_label(params: dict[str, Any]) -> str:
    if not params:
        return "defaults"
    return " ".join(f"{k}={params[k]}" for k in sorted(params))


def _lower_ci(outcomes: list[Any]) -> float | None:
    r = np.array([o.r for o in outcomes], dtype="float64")
    if r.size < 2:
        return None
    sd = float(r.std(ddof=1))
    if sd <= 0:
        return None
    return float(r.mean() - 1.64 * sd / math.sqrt(r.size))  # 90% one-sided


def grade_window(
    bars: Bars,
    sig_i: np.ndarray,
    side: np.ndarray,
    stops: np.ndarray,
    tp_r: np.ndarray,
    lo: int,
    hi: int,
    max_hold_bars: int,
    seed: int,
) -> dict[str, Any]:
    """Net, gross, placebo and 1.5x-cost readings for the trades signalled inside [lo, hi)."""
    mask = (sig_i >= lo) & (sig_i < hi)
    s_sig, s_side, s_stops, s_tp = sig_i[mask], side[mask], stops[mask], tp_r[mask]
    outcomes = simulate(bars, s_sig, s_side, s_stops, s_tp, max_hold_bars)
    net = summarize(outcomes)
    gross = summarize(simulate(gross_bars(bars), s_sig, s_side, s_stops, s_tp, max_hold_bars))
    placebo_mean: float | None = None
    stressed_mean: float | None = None
    if s_sig.size:
        p_sig, p_side = matched_placebo(bars, s_sig, s_side, seed)
        placebo_mean = summarize(simulate(bars, p_sig, p_side, s_stops, s_tp, max_hold_bars))[
            "net_r_mean"
        ]
        stressed_mean = summarize(
            simulate(
                bars,
                s_sig,
                s_side,
                s_stops,
                s_tp,
                max_hold_bars,
                spread_pips=bars.spread_pips() * 1.5,
            )
        )["net_r_mean"]
    mean = net["net_r_mean"]
    passes = bool(
        mean is not None
        and mean > 0
        and net["n"] >= MIN_TRADES
        and placebo_mean is not None
        and mean > placebo_mean
        and stressed_mean is not None
        and stressed_mean > 0
    )
    return {
        "n": net["n"],
        "net_r_mean": mean,
        "gross_r_mean": gross["net_r_mean"],
        "placebo_r_mean": placebo_mean,
        "stressed_r_mean": stressed_mean,
        "lower_ci_r": _lower_ci(outcomes),
        # summarize() omits win_rate on an empty window, which is a legitimate outcome.
        "win_rate": net.get("win_rate"),
        "passes_gates": passes,
    }


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else format(value, "+.3f")


def _write_report(
    base: Path,
    payload: dict[str, Any],
    rows: list[dict[str, Any]],
    summary: list[dict[str, Any]],
    pooled: list[dict[str, Any]],
) -> None:
    base.with_suffix(".json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = [
        f"# Regime test: {payload['family']} on {payload['timeframe']}",
        "",
        f"- Generated: {payload['generated_utc']} (UTC)",
        (
            f"- Data: {payload['bars']} bars, {payload['first_utc']} .. {payload['last_utc']}; "
            f"max hold {payload['max_hold_bars']} bars"
        ),
        (
            f"- Variants: every combination in the family grid "
            f"({payload['variant_count']}), not the train-best"
        ),
        (
            "- Gates per window: mean R > 0, beats the side-flipped placebo, survives 1.5x "
            f"costs, n >= {payload['min_trades']}"
        ),
        (
            "- Costs: one global median spread charged entry and exit, in R - the same trades "
            "at zero cost are in the Gross column"
        ),
        f"- Excluded: {payload['excluded']}",
        "",
        "## Per regime",
        "",
        "| Regime | Window | Carry sign | Variants | Positive | Passing gates | Best | Worst | Verdict |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in summary:
        lines.append(
            f"| {s['regime']} | {s['window']} | {s['carry_signs']} | {s['variants']} | "
            f"{s['variants_positive']} | {s['variants_passing_gates']} | "
            f"{_fmt(s['best_variant_mean'])} | {_fmt(s['worst_variant_mean'])} | {s['verdict']} |"
        )
    lines += [
        "",
        "## Every variant in every window",
        "",
        "| Regime | Variant | n | Net (R) | Gross (R) | Placebo (R) | 1.5x costs (R) | Lower 90% CI | Gates |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['regime']} | {r['variant']} | {r['n']} | {_fmt(r['net_r_mean'])} | "
            f"{_fmt(r['gross_r_mean'])} | {_fmt(r['placebo_r_mean'])} | "
            f"{_fmt(r['stressed_r_mean'])} | {_fmt(r['lower_ci_r'])} | "
            f"{'pass' if r['passes_gates'] else 'no'} |"
        )
    lines += [
        "",
        "## Pooled over every window with verified rate coverage (1973-04 .. 2026-10)",
        "",
        "| Variant | n | Net (R) | Gross (R) | Placebo (R) | 1.5x costs (R) | Lower 90% CI | Gates |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for p in pooled:
        lines.append(
            f"| {p['variant']} | {p['n']} | {_fmt(p['net_r_mean'])} | {_fmt(p['gross_r_mean'])} | "
            f"{_fmt(p['placebo_r_mean'])} | {_fmt(p['stressed_r_mean'])} | "
            f"{_fmt(p['lower_ci_r'])} | {'pass' if p['passes_gates'] else 'no'} |"
        )
    present = sum(1 for s in summary if s["verdict"] == "edge present")
    lines += [
        "",
        f"**Verdict**: the rule is positive on every variant in {present} of {len(summary)} regimes.",
    ]
    base.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(
    timeframe: str = "D1",
    family_id: str = "carry_trend",
    limit: int | None = None,
    max_hold_bars: int = 120,
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

    families: list[Family] = [*FAMILIES, *SCALP_FAMILIES]
    family = next((f for f in families if f["id"] == family_id), None)
    if family is None:
        print(f"Unknown family {family_id!r}; expected one of {sorted(f['id'] for f in families)}")
        return 2
    grid = GRIDS.get(family_id)
    variants: list[dict[str, Any]] = [dict(p) for p in grid] if grid else [{}]
    fn = family["fn"]

    mt5 = ReadOnlyMT5(backend)
    mt5.connect(
        login=settings.login,
        password=settings.password,
        server=settings.server,
        terminal_path=settings.terminal_path,
    )
    stamp = datetime.now(UTC)
    rows: list[dict[str, Any]] = []
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
        first = datetime.fromtimestamp(int(bars.time[0]), UTC).date().isoformat()
        last = datetime.fromtimestamp(int(bars.time[-1]), UTC).date().isoformat()
        print(f"{family_id} on {timeframe}: {bars.n} bars, {first} .. {last}")
        print(
            f"{len(variants)} grid variant(s); median spread "
            f"{np.median(bars.spread_pips()):.2f} pips (one global median spread is what the "
            "cost model charges, so the 1970s-1990s bars are billed a modern spread)"
        )

        # One pass over the whole series per variant; every window is then a slice of it.
        signals: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = {}
        for params in variants:
            signals[_params_label(params)] = _family_call(fn, bars, dict(params), None)

        bounds = [(r, *_bar_bounds(bars, r.start, r.end)) for r in REGIMES]
        for regime, lo, hi in bounds:
            signs = _carry_signs(bars, lo, hi)
            for params in variants:
                label = _params_label(params)
                sig_i, side, stops, tp_r = signals[label]
                res = grade_window(bars, sig_i, side, stops, tp_r, lo, hi, max_hold_bars, seed=11)
                rows.append(
                    {
                        "regime": regime.label,
                        "window": f"{regime.start[:7]}..{regime.end[:7]}",
                        "note": regime.note,
                        "carry_signs": signs,
                        "variant": label,
                        "params": params,
                        **res,
                    }
                )
                print(
                    f"  {regime.label:38s} {label:24s} n={res['n']:4d} "
                    f"net={_fmt(res['net_r_mean'])} gross={_fmt(res['gross_r_mean'])} "
                    f"placebo={_fmt(res['placebo_r_mean'])} "
                    f"1.5x={_fmt(res['stressed_r_mean'])} "
                    f"lower_ci={_fmt(res['lower_ci_r'])} carry={signs:3s} "
                    f"-> {'PASS' if res['passes_gates'] else 'no'}"
                )

        # Regime verdicts: the rule carries a regime only if every variant is positive in it.
        summary: list[dict[str, Any]] = []
        for regime in REGIMES:
            cell = [r for r in rows if r["regime"] == regime.label]
            if not cell:
                continue
            positive = sum(1 for r in cell if (r["net_r_mean"] or 0) > 0)
            gates = sum(1 for r in cell if r["passes_gates"])
            summary.append(
                {
                    "regime": regime.label,
                    "window": cell[0]["window"],
                    "carry_signs": cell[0]["carry_signs"],
                    "variants": len(cell),
                    "variants_positive": positive,
                    "variants_passing_gates": gates,
                    "best_variant_mean": max((r["net_r_mean"] or 0.0) for r in cell),
                    "worst_variant_mean": min((r["net_r_mean"] or 0.0) for r in cell),
                    "verdict": (
                        "edge present" if positive == len(cell) else "mixed" if positive else "no edge"
                    ),
                }
            )

        # Pooled reading over every window that carries verified rate coverage.
        pooled_lo = bounds[0][1]
        pooled_hi = bounds[-1][2]
        pooled: list[dict[str, Any]] = []
        for params in variants:
            label = _params_label(params)
            sig_i, side, stops, tp_r = signals[label]
            res = grade_window(
                bars, sig_i, side, stops, tp_r, pooled_lo, pooled_hi, max_hold_bars, seed=11
            )
            pooled.append({"variant": label, "params": params, **res})

        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        base = REPORT_DIR / f"regimes-{stamp.strftime('%Y%m%d-%H%M%S')}"
        payload: dict[str, Any] = {
            "generated_utc": stamp.isoformat(timespec="seconds"),
            "family": family_id,
            "timeframe": timeframe,
            "bars": int(bars.n),
            "first_utc": first,
            "last_utc": last,
            "max_hold_bars": max_hold_bars,
            "min_trades": MIN_TRADES,
            "variant_count": len(variants),
            "excluded": "bars before 1973-04 (no verified rate-table row; USDJPY pegged until 1971)",
            "regimes": summary,
            "rows": rows,
            "pooled": pooled,
        }
        _write_report(base, payload, rows, summary, pooled)
        print(f"Report: {base.with_suffix('.md')}")
    finally:
        mt5.shutdown()
    present = sum(1 for s in summary if s["verdict"] == "edge present")
    print(f"Verdict: positive on every variant in {present} of {len(summary)} regimes.")
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Regime stability test for a strategy family")
    parser.add_argument("timeframe", nargs="?", default="D1", choices=sorted(TIMEFRAMES))
    parser.add_argument("family", nargs="?", default="carry_trend")
    parser.add_argument("--bars", type=int, default=None)
    parser.add_argument("--max-hold", type=int, default=120)
    args = parser.parse_args()
    raise SystemExit(run(args.timeframe, args.family, args.bars, args.max_hold))
