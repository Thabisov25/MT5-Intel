"""Run the read-only diagnostic: python -m mt5_intel. Exit 0 = PASS/WARN, 1 = FAIL/NOT_VERIFIED, 2 = setup error."""

from __future__ import annotations

import sys
from datetime import UTC, datetime

from .config import ConfigError, load_settings
from .diagnostic import PASS, WARN, run_diagnostic
from .report import write_report


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Config error: {exc}")
        return 2
    try:
        import MetaTrader5 as backend
    except ImportError:
        print("MetaTrader5 package not installed (Windows only). Run: pip install MetaTrader5")
        return 2
    stamp = datetime.now(UTC)
    try:
        report = run_diagnostic(settings, backend, now=stamp)
    except ConnectionError as exc:
        print(f"Connection error: {exc}")
        print("Check MT5_LOGIN, MT5_SERVER and the investor password in .env; is the terminal reachable?")
        return 2
    json_path, md_path = write_report(report, settings.report_dir, stamp)
    counts: dict[str, int] = {}
    for check in report.checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    print(f"Overall: {report.overall}  ({', '.join(f'{k}={v}' for k, v in sorted(counts.items()))})")
    print(f"Report: {md_path}")
    print(f"JSON:   {json_path}")
    return 0 if report.overall in (PASS, WARN) else 1


if __name__ == "__main__":
    sys.exit(main())
