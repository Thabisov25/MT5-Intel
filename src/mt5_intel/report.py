"""Write the diagnostic report as JSON and Markdown. Credentials are never written."""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from .diagnostic import Report


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "item"):  # numpy scalar
        return _clean(value.item())
    return value


def to_markdown(report: Report) -> str:
    lines = [
        "# MT5 milestone 1 diagnostic",
        "",
        f"- Generated (UTC): {report.generated_utc}",
        f"- Overall: **{report.overall}**",
        "- Read-only: no orders were placed or modified.",
        "",
        "## Checks",
        "",
        "| Area | Check | Status | Detail |",
        "|---|---|---|---|",
    ]
    for check in report.checks:
        lines.append(f"| {check.area} | {check.name} | {check.status} | {check.detail} |")
    risk = report.data.get("risk")
    if risk:
        lines += [
            "",
            "## Risk fit",
            "",
            (
                f"Budget: {risk['risk_pct']:g}% of R{risk['reference_equity_zar']:g} "
                f"= {risk['risk_budget_usc']:.2f} USC (R{risk['risk_budget_zar']:.2f})."
            ),
            "",
            "| Stop (pips) | Risk per min lot (R) | Max volume | Actual risk (R) | Fits |",
            "|---|---|---|---|---|",
        ]
        for row in risk["table"]:
            lines.append(
                f"| {row['stop_pips']:g} | {row['risk_per_min_lot_zar']:.2f} | "
                f"{row['max_volume']:g} | {row['actual_risk_zar']:.2f} | "
                f"{'yes' if row['fits'] else 'no (unsuitable)'} |"
            )
    spread = report.data.get("spread_by_hour")
    if spread:
        label = "UTC" if spread["utc_classified"] else "server time (offset unknown)"
        lines += [
            "",
            f"## Spread by hour ({label}, {spread['bar_timeframe']} bars)",
            "",
            spread["note"],
            "",
            ("| Hour UTC | Hour SAST | Samples | Median pips | P90 pips |"),
            "|---|---|---|---|---|",
        ]
        for hour in spread["hours"]:
            median = "n/a" if hour["median_pips"] is None else f"{hour['median_pips']:.2f}"
            p90 = "n/a" if hour["p90_pips"] is None else f"{hour['p90_pips']:.2f}"
            lines.append(
                f"| {hour['hour_utc']:02d} | {hour['hour_sast']:02d} | {hour['samples']} | {median} | {p90} |"
            )
    return "\n".join(lines) + "\n"


def write_report(report: Report, report_dir: Path, stamp: datetime) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    base = report_dir / f"diagnostic-{stamp.strftime('%Y%m%d-%H%M%S')}"
    payload = _clean(
        {
            "generated_utc": report.generated_utc,
            "overall": report.overall,
            "checks": [check.__dict__ for check in report.checks],
            "data": report.data,
        }
    )
    json_path = base.with_suffix(".json")
    md_path = base.with_suffix(".md")
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path.write_text(to_markdown(report), encoding="utf-8")
    return json_path, md_path
