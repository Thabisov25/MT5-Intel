"""Install this project's chart indicator into the MT5 terminal: copy, compile, say what's next.

The chart does not run `tools\\mt5\\Mt5IntelSignals.mq5` - it runs whatever copy sits in the
terminal's `MQL5\\Indicators`, compiled by MetaEditor. That copy goes stale the moment the source
here changes, and a stale build looks exactly like a broken fix: "nothing changed on the chart".
Removing that confusion is the one thing this script exists for:

    .\\.venv\\Scripts\\python.exe -m mt5_intel.install_indicator

It copies the source over the terminal's copy, compiles it there, re-exports the chart files
the indicator reads (the same set the watcher keeps current), and prints the version the
chart's key must then show - so "is the chart running my change?" has an answer printed on the
chart itself. One run leaves both halves in step: the build in the terminal and the files
under it. It writes only the indicator's own files (.mq5, .ex5 and the compile log) and those
exports; nothing else in the terminal folder is touched.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from .config import ConfigError, Settings, load_settings, watch_symbols
from .mt5_adapter import ReadOnlyMT5
from .research import export_mt5
from .research.backtest import load_bars, resolve_offset_s
from .watch import DISPLAY_TIMEFRAMES, WATCH_TIMEFRAME

INDICATOR = "Mt5IntelSignals.mq5"
SOURCE = Path(__file__).resolve().parents[2] / "tools" / "mt5" / INDICATOR


def install(source: Path, data_folder: Path) -> Path:
    """Copy the source over the terminal's own copy; returns the path the chart loads from.

    The target is the copy MetaEditor compiles, so installing here is what makes an edit to the
    source visible on a chart at all.
    """
    target = data_folder / "MQL5" / "Indicators" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return target


def find_data_folder(root: Path | None = None) -> Path | None:
    """The terminal's data folder: the one this project's exports already land in.

    `%APPDATA%\\MetaQuotes\\Terminal` holds one hashed folder per terminal instance, and more
    than one is easy to end up with - so the folder holding `MQL5\\Files\\mt5intel_*.csv` is the
    one the exporter writes to and the chart reads from, and that is the one to install into.
    With no such folder the most recently used terminal folder is the best guess, and
    `--data-folder` overrides all of it.
    """
    if root is None:
        appdata = os.environ.get("APPDATA")
        if not appdata:
            return None
        root = Path(appdata) / "MetaQuotes" / "Terminal"
    if not root.is_dir():
        return None
    folders = [p for p in root.iterdir() if p.is_dir() and (p / "MQL5").is_dir()]
    exporting = [p for p in folders if any((p / "MQL5" / "Files").glob("mt5intel_*.csv"))]
    if exporting:
        return max(exporting, key=lambda p: p.stat().st_mtime)
    if not folders:
        return None
    return max(folders, key=lambda p: p.stat().st_mtime)


def origin_install_dir(origin: Path) -> Path | None:
    """The terminal install folder `origin.txt` names.

    The terminal writes this file UTF-16 with a BOM; reading it as plain UTF-8 interleaves the
    path with NUL characters, and MetaEditor is then looked for under a name nothing has.
    """
    try:
        raw = origin.read_bytes()
    except OSError:
        return None
    text = (
        raw.decode("utf-16", errors="ignore")
        if raw.startswith((b"\xff\xfe", b"\xfe\xff"))
        else raw.decode("utf-8", errors="ignore")
    )
    text = text.strip()
    return Path(text) if text else None


def find_metaeditor(data_folder: Path, override: Path | None = None) -> Path | None:
    """The MetaEditor that belongs to this terminal, else a standard install's.

    The data folder's `origin.txt` records the terminal's install folder - the same install
    made this data folder, so its MetaEditor64.exe is the one to compile with. Brokers rename
    that folder ("HFM Metatrader 5", not "MetaTrader 5"), which is why the standard paths are
    only the fallback.
    """
    candidates: list[Path] = []
    if override is not None:
        candidates.append(override)
    origin = data_folder / "origin.txt"
    if origin.is_file():
        install_dir = origin_install_dir(origin)
        if install_dir is not None:
            candidates.append(install_dir / "MetaEditor64.exe")
    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        base = os.environ.get(env)
        if base:
            candidates.append(Path(base) / "MetaTrader 5" / "MetaEditor64.exe")
    which = shutil.which("MetaEditor64.exe")
    if which:
        candidates.append(Path(which))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def compile_result(log_text: str) -> tuple[int, int] | None:
    """`(errors, warnings)` a MetaEditor compile log reports, or None when it reports nothing.

    The last summary in the log is the one that counts - the log lists each file it touched
    before wrapping up with the total.
    """
    matches = re.findall(r"(\d+)\s+errors?,\s*(\d+)\s+warnings?", log_text, flags=re.IGNORECASE)
    if not matches:
        return None
    errors, warnings = matches[-1]
    return int(errors), int(warnings)


def run_metaeditor(metaeditor: Path, mq5: Path) -> Path | None:
    """Compile the indicator in the terminal's folder; returns the log MetaEditor wrote."""
    log = mq5.with_suffix(".log")
    log.unlink(missing_ok=True)
    try:
        subprocess.run(
            [str(metaeditor), f"/compile:{mq5}", f"/log:{log}"],
            check=False,
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return log if log.is_file() else None


def export_chart_files(mt5: ReadOnlyMT5, settings: Settings) -> int:
    """Rewrite every chart file the watcher keeps current, from the code the watcher uses.

    The symbols are the watch list and the timeframes the watch loop's own, so this cannot drift
    into refreshing one chart's files while the watcher refreshes another's - and the files land
    with the completeness stamp, so the freshly built indicator reads them all-or-nothing.
    Returns the number of exports that did not happen.
    """
    failures = 0
    for symbol in watch_symbols(settings):
        offset_s = resolve_offset_s(mt5, settings, symbol)
        for timeframe in (WATCH_TIMEFRAME, *DISPLAY_TIMEFRAMES):
            try:
                bars = load_bars(
                    mt5, symbol, timeframe, export_mt5.DEFAULT_BARS, utc_offset_s=offset_s
                )
                if bars is None or export_mt5.export(
                    mt5, bars, symbol, timeframe, offset_s=offset_s
                ) is None:
                    failures += 1
                    continue
                print(f"  {symbol} {timeframe}: the three files are rewritten")
            except Exception:  # noqa: BLE001 - one symbol's fault is not another's silence
                failures += 1
    return failures


def declared_version(source: Path) -> str | None:
    """The version `#property` declares - what the chart's key must be showing afterwards."""
    match = re.search(r'#property version\s+"([^"]+)"', source.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def run_export() -> int:
    """Re-export the chart files from the terminal; 0 when every one was rewritten."""
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"  Config error: {exc}")
        return 1
    try:
        import MetaTrader5 as backend
    except ImportError:
        print("  MetaTrader5 package not installed (Windows only)")
        return 1
    mt5 = ReadOnlyMT5(backend)
    mt5.connect(
        login=settings.login,
        password=settings.password,
        server=settings.server,
        terminal_path=settings.terminal_path,
    )
    try:
        failures = export_chart_files(mt5, settings)
    finally:
        mt5.shutdown()
    if failures:
        print(f"  {failures} export(s) did not happen - those files are not in step yet")
    else:
        print("  every chart file rewritten, stamped and current")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copy Mt5IntelSignals.mq5 into the terminal's MQL5\\Indicators and compile it"
    )
    parser.add_argument(
        "--data-folder",
        type=Path,
        default=None,
        help="the terminal's data folder (default: found under %APPDATA%\\MetaQuotes\\Terminal)",
    )
    parser.add_argument(
        "--metaeditor", type=Path, default=None, help="MetaEditor64.exe to compile with"
    )
    parser.add_argument(
        "--no-export",
        action="store_true",
        help="skip re-exporting the chart files (install the build only)",
    )
    args = parser.parse_args()

    if not SOURCE.is_file():
        print(f"no {SOURCE} in this project - nothing to install")
        return 1

    data_folder = args.data_folder
    if data_folder is None:
        data_folder = find_data_folder()
        if data_folder is None:
            print("could not find the terminal's data folder under %APPDATA%\\MetaQuotes\\Terminal")
            print("open MT5 once (File > Open Data Folder shows it), then run again with:")
            print("  python -m mt5_intel.install_indicator --data-folder \"C:\\...\\Terminal\\<hash>\"")
            return 1
    if not data_folder.is_dir():
        print(f"{data_folder} is not a folder - pass the terminal's data folder")
        return 1

    target = install(SOURCE, data_folder)
    print(f"copied {SOURCE.name} -> {target}")

    metaeditor = find_metaeditor(data_folder, args.metaeditor)
    if metaeditor is None:
        print("MetaEditor not found - the copy is in place, but it still has to be compiled:")
        print(f"open {target} in MetaEditor and press F7 (or pass --metaeditor)")
        return 1

    started = time.time()
    log = run_metaeditor(metaeditor, target)
    result = compile_result(log.read_text(encoding="utf-8", errors="ignore")) if log else None
    if result is not None:
        errors, warnings = result
        if errors > 0:
            print(f"{errors} error(s) - the chart keeps the build it has. Compile output:")
            assert log is not None
            for line in log.read_text(encoding="utf-8", errors="ignore").splitlines()[-10:]:
                print(f"  {line}")
            return 1
        print(f"compiled with {errors} errors, {warnings} warnings ({metaeditor.name})")
    else:
        built = target.with_suffix(".ex5").is_file() and target.with_suffix(".ex5").stat().st_mtime >= started
        if not built:
            print(f"{metaeditor.name} produced no .ex5 and no log to say why")
            return 1
        print(f"compiled ({metaeditor.name} wrote no log to count errors from)")

    export_failures = 0
    if not args.no_export:
        print()
        print("Re-exporting the chart files the indicator reads ...")
        export_failures = run_export()

    version = declared_version(SOURCE)
    print()
    print("Next, in MT5: Ctrl+I -> remove Mt5IntelSignals -> drag it on again from the Navigator")
    print("(or just restart MT5 - the terminal keeps running the build it already loaded).")
    if version:
        print("The key's title line on the chart must then read:")
        print(f"    MT5 intel - signals v{version} (this indicator places no orders)")
    return 1 if export_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
