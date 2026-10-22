from __future__ import annotations

import re
from datetime import UTC
from pathlib import Path

import pytest

from fakes import FakeMT5
from mt5_intel.mt5_adapter import ReadOnlyMT5

SRC = Path(__file__).resolve().parents[1] / "src"
EXECUTION = SRC / "mt5_intel" / "execution.py"
ORDER_PATTERN = re.compile(
    r"order_send|order_check|order_calc|OrderSend|OrderCheck|TRADE_ACTION|"
    r"position_close|position_modify|TradeRequest|\border_\w+\(|\.order_",
)


def test_adapter_exposes_no_order_methods() -> None:
    public = [name for name in dir(ReadOnlyMT5) if not name.startswith("_")]
    assert public, "adapter should expose its read methods"
    for name in public:
        assert not ORDER_PATTERN.search(name), f"order-related method exposed: {name}"
        assert "send" not in name and "close" not in name and "modify" not in name


def test_source_contains_no_order_calls_outside_the_execution_module() -> None:
    """The read-only promise, re-scoped rather than dropped.

    Auto-trading needed a way to send an order, so exactly one module now has one. This scan is
    what keeps that "exactly one" true: every other file under src/ must still be free of the
    vocabulary, so research, reporting, the journal and the dashboard-facing code cannot grow an
    order path of their own by accident. `execution` is the only exception, and it is named here
    rather than inferred, so adding a second one has to be a deliberate edit to this test.
    """
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        if path.resolve() == EXECUTION.resolve():
            continue
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            if ORDER_PATTERN.search(line):
                offenders.append(f"{path.relative_to(SRC)}:{number}: {line.strip()}")
    assert offenders == [], "order-related calls found:\n" + "\n".join(offenders)


def test_the_execution_module_is_the_exception_because_it_really_sends_orders() -> None:
    """The other half of the scan: the allowed module must actually contain the calls.

    Without this, a rename that left `execution` empty would make the test above pass on a
    codebase where nothing can trade at all - which is a different project, not this one.
    """
    lines = EXECUTION.read_text(encoding="utf-8").splitlines()
    assert any(ORDER_PATTERN.search(line) for line in lines)


def test_the_order_surface_is_exactly_the_methods_that_trade() -> None:
    """The trader's public surface, pinned.

    `ReadOnlyMT5` is held to "exposes nothing that can change a position"; this class is the
    opposite by design, so it is held to "exposes nothing beyond what opening and closing a trade
    needs". An exact set, not a subset: growing a modify, a partial close or a pending-order call
    should fail here and be argued for.

    `terminal_info` was added to the set deliberately, and it is the only addition since this
    class was written: the Algo Trading button is the terminal's own permission to trade and is not
    in the account, so without asking it every order goes out to be refused with retcode 10027.
    It reads, like the rest of the getters here - the two methods that can change anything are still
    only `placed` and `closed`.

    `modified` is the next deliberate addition, and the first one that can change a position:
    stays-alive management needs to move a live trade's stop or its target, and `modify_sl`/
    `modify_tp` each only ever touch one of the two. `modified` sets either or both in a single
    request, so a breakeven shift and a target hand-off are two lines, not two broker round-trips.
    """
    from mt5_intel.execution import MT5Trader

    public = {name for name in dir(MT5Trader) if not name.startswith("_")}
    assert public == {
        "account_info",
        "terminal_info",
        "symbol_info",
        "positions_get",
        "symbol_info_tick",
        "placed",
        "closed",
        "modified",
    }


def test_adapter_reads_positions_without_exposing_a_way_to_change_them() -> None:
    from datetime import datetime

    backend = FakeMT5(now=datetime(2026, 10, 7, 10, tzinfo=UTC), positions=2)
    adapter = ReadOnlyMT5(backend)
    assert len(adapter.positions_get()) == 2
    assert len(adapter.positions_get("USDJPYc")) == 2
    assert adapter.positions_get("EURUSDc") == ()


def test_adapter_rejects_unlisted_timeframes() -> None:
    from datetime import datetime

    backend = FakeMT5(now=datetime(2026, 10, 7, 10, tzinfo=UTC))
    adapter = ReadOnlyMT5(backend)
    # M1..D1 are allow-listed (read-only rate copies); W1 is not.
    with pytest.raises(ValueError, match="W1"):
        adapter.copy_rates_from_pos("USDJPYc", "W1", 0, 10)


def test_public_surface_does_not_expose_raw_backend() -> None:
    # Guard against accidental use. The backend sits in a name-mangled private attribute, so this
    # is a convention and a source scan, not a sandbox. Only the public surface is checked here.
    from datetime import datetime

    backend = FakeMT5(now=datetime(2026, 10, 7, 10, tzinfo=UTC))
    adapter = ReadOnlyMT5(backend)
    public = [n for n in dir(adapter) if not n.startswith("_")]
    assert not any(getattr(adapter, n) is backend for n in public)


def test_adapter_reads_deal_history_without_exposing_a_way_to_change_it() -> None:
    from datetime import datetime
    from types import SimpleNamespace

    deal = SimpleNamespace(position_id=7, entry=1, type=1, price=150.0, profit=2.0)
    backend = FakeMT5(now=datetime(2026, 10, 7, 10, tzinfo=UTC), deals=[deal])
    adapter = ReadOnlyMT5(backend)
    assert len(adapter.history_deals_get(0, 1_000_000)) == 1
    assert adapter.history_deals_get(0, 1_000_000)[0].position_id == 7
    # A terminal that will not answer reads as nothing, which is what the journal expects of it.
    silent = FakeMT5(now=datetime(2026, 10, 7, 10, tzinfo=UTC), deals_unavailable=True)
    assert ReadOnlyMT5(silent).history_deals_get(0, 1_000_000) == ()
