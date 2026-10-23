"""Tests for the indicator installer: what reaches the terminal has to be this project's build.

The chart runs the compiled copy in the terminal's `MQL5\\Indicators`, not the source here - so
"the fix did nothing" and "the old build is still attached" look exactly the same on a chart.
The installer closes that gap; these tests pin the parts that can be checked without a
terminal: which folder it picks, what it writes there, and that the source itself points at the
command - the note that makes the workflow discoverable without the README.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fakes import FakeMT5
from mt5_intel import install_indicator
from mt5_intel.config import Settings
from mt5_intel.mt5_adapter import ReadOnlyMT5
from mt5_intel.research import export_mt5
from mt5_intel.watch import DISPLAY_TIMEFRAMES, WATCH_TIMEFRAME
from test_export_mt5 import WEDNESDAY_10_UTC, make_bars


def test_install_overwrites_the_terminals_stale_copy(tmp_path: Path) -> None:
    """The terminal's copy is the one the chart compiles and runs; it has to become this one."""
    data = tmp_path / "ABCDEF0123456789ABCDEF0123456789"
    stale = data / "MQL5" / "Indicators" / install_indicator.INDICATOR
    stale.parent.mkdir(parents=True)
    stale.write_text("the old build's source\n", encoding="utf-8")
    source = tmp_path / "project" / "tools" / "mt5" / install_indicator.INDICATOR
    source.parent.mkdir(parents=True)
    source.write_text("// the fixed source\n", encoding="utf-8")

    target = install_indicator.install(source, data)

    assert target == stale
    assert stale.read_text(encoding="utf-8") == "// the fixed source\n"


def test_the_data_folder_picked_is_the_one_the_exports_land_in(tmp_path: Path) -> None:
    """More than one terminal folder is easy to end up with; the chart reads the one we export to."""
    idle = tmp_path / "IDLEHASH"
    (idle / "MQL5" / "Indicators").mkdir(parents=True)
    live = tmp_path / "LIVEHASH"
    (live / "MQL5" / "Files").mkdir(parents=True)
    (live / "MQL5" / "Files" / "mt5intel_USDJPY_H1.csv").write_text("#rows,0,0\n", encoding="utf-8")

    assert install_indicator.find_data_folder(tmp_path) == live

    # And nothing guessed when there is nothing to guess from.
    assert install_indicator.find_data_folder(tmp_path / "nope") is None


def test_metaeditor_is_found_through_the_terminals_own_origin_marker(tmp_path: Path) -> None:
    """A broker renames the install folder, so the marker the terminal writes is the address.

    `origin.txt` is UTF-16 - reading it as UTF-8 turns the path into nulls and MetaEditor is
    then looked for under a name nothing has.
    """
    install_dir = tmp_path / "Program Files" / "HFM Metatrader 5"
    install_dir.mkdir(parents=True)
    metaeditor = install_dir / "MetaEditor64.exe"
    metaeditor.write_bytes(b"stub")
    data = tmp_path / "LIVEHASH"
    data.mkdir()
    (data / "origin.txt").write_bytes(str(install_dir).encode("utf-16"))

    assert install_indicator.origin_install_dir(data / "origin.txt") == install_dir
    assert install_indicator.find_metaeditor(data) == metaeditor


def test_a_compile_log_reports_how_it_came_out() -> None:
    """MetaEditor's summary is the only honest answer to \"did it compile\" - the exit code is not."""
    assert install_indicator.compile_result(
        "Mt5IntelSignals.mq5 : 0 errors, 2 warnings, 120 msec elapsed\n"
    ) == (0, 2)
    assert install_indicator.compile_result("Result: 3 errors, 1 warnings, 90 msec\n") == (3, 1)
    assert install_indicator.compile_result("nothing usable in here") is None


def test_one_run_also_re_exports_the_chart_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The install's promise is both halves: the build in the terminal and the files it reads.

    The symbols come from the watch list and the timeframes from the watch loop's own, so the
    files this refreshes are the files the watcher would - and each lands carrying the
    completeness stamp the freshly built indicator trusts.
    """
    bars = make_bars()
    monkeypatch.setattr(install_indicator, "load_bars", lambda *_args, **_kwargs: bars)
    backend = FakeMT5(now=WEDNESDAY_10_UTC, symbol_name="USDJPY", data_path=str(tmp_path))
    adapter = ReadOnlyMT5(backend)
    settings = Settings(
        login=12345678,
        password="unit-test-secret",
        server="HFMarketsSV-Demo",
        terminal_path=None,
        utc_offset_hours=2.0,
        symbol_root="USDJPY",
        reference_equity_zar=100.0,
        risk_pct=1.0,
        usd_zar_fallback=18.0,
        history_bars=600,
        spread_days=2,
        tick_watch_seconds=0.0,
        report_dir=tmp_path,
    )

    failures = install_indicator.export_chart_files(adapter, settings)

    assert failures == 0
    folder = tmp_path / "MQL5" / "Files"
    written = sorted(p.name for p in folder.iterdir())
    expected = sorted(
        name
        for tf in (WATCH_TIMEFRAME, *DISPLAY_TIMEFRAMES)
        for name in (
            export_mt5.filename(tf, "USDJPY"),
            export_mt5.lines_filename(tf, "USDJPY"),
            export_mt5.context_filename(tf, "USDJPY"),
        )
    )
    assert written == expected
    for name in written:
        first = (folder / name).read_text(encoding="utf-8").splitlines()[0]
        assert first.startswith("#rows,"), f"{name} carries the completeness stamp"


def test_the_source_says_how_to_install_it_without_the_readme() -> None:
    """The note inside the file: the chart runs a copy, and this is the command that updates it."""
    source = install_indicator.SOURCE
    text = source.read_text(encoding="utf-8")
    assert "mt5_intel.install_indicator" in text, "the file names its own installer"
    assert "MQL5\\Indicators" in text, "and says the chart runs a copy, not this file"
    assert install_indicator.declared_version(source) is not None
