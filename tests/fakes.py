"""Fake MetaTrader5 backend for offline tests. Server clock = UTC + 3 h, as with HFM in summer.

Mirrors the real terminal where it matters: copy_rates_* returns None with error (-2, 'Invalid params')
for a request above terminal maxbars, and symbol specs can read as 0 right after connecting.
"""

from __future__ import annotations

import fnmatch
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import numpy as np

SERVER_OFFSET_S = 3 * 3600
RATE_DTYPE = np.dtype(
    [
        ("time", "<i8"),
        ("open", "<f8"),
        ("high", "<f8"),
        ("low", "<f8"),
        ("close", "<f8"),
        ("spread", "<i4"),
    ]
)
INVALID_PARAMS = (-2, "Terminal: Invalid params")


class FakeMT5:
    TIMEFRAME_M1 = 1
    TIMEFRAME_M5 = 5
    TIMEFRAME_M15 = 15
    TIMEFRAME_M30 = 30
    TIMEFRAME_H1 = 16385

    # The order vocabulary the trader adapter reads off the module. The same names the real
    # package uses, so a test here fails the same way the real thing would.
    TRADE_ACTION_DEAL = 1
    # The real module's value; the management pass moves stops through this action.
    TRADE_ACTION_SLTP = 2
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    # The real module's values. A broker that takes one filling mode and not another is the case
    # that refused every order this project ever sent (retcode 10030), so the fake carries all
    # three names even though only IOC is the default it answers to.
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 3
    TRADE_RETCODE_DONE = 10009

    def __init__(
        self,
        *,
        now: datetime,
        currency: str = "USC",
        trade_mode: int = 0,
        symbol_name: str = "USDJPYc",
        # A tick per read makes ages move between reads without the test touching anything.
        advance_ticks: bool = True,
        init_ok: bool = True,
        usd_zar_symbol: bool = False,
        maxbars: int = 100_000,
        # The first N symbol_info reads answer with a value of 0 - what a terminal taken out of
        # deep idle answers, and how much of the read path has to survive it (D-004).
        spec_warmup_reads: int = 0,
        no_history: bool = False,
        positions: int = 0,
        equity: float = 1000.0,
        balance: float = 1000.0,
        data_path: str | None = None,
        deals: list[Any] | None = None,
        deals_unavailable: bool = False,
        order_retcode: int | None = None,
        # Per-send answers, consumed one per order_send - so a test can rehearse the sequence the
        # live terminal produced: a 10021 "No prices" that a re-read price then fills. When the
        # list runs out the last value repeats, so a retry that fires too often fails the test's
        # call-count assertion rather than silently succeeding.
        order_retcodes: list[int] | None = None,
        order_comment: str = "Done",
        order_raises: bool = False,
        tick: bool = True,
        # The tick's own bid/ask level. USDJPY's 150.100/150.114 is the default only because the
        # earliest tests were written on the JPY pair; a fake driving a euro card must quote the
        # euro or every freshness check measures 150.11 - 1.12 = 149 "pips of slippage", which is
        # not a market, it is a currency mismatch inside the test double.
        tick_bid: float = 150.100,
        tick_ask: float = 150.114,
        # Whether the terminal reports trading as allowed; None pretends the read only happens
        # through `terminal_info()` calls that a caller has to treat as its failure.
        terminal_trade_allowed: bool | None = True,
    ) -> None:
        self.now_epoch = int(now.timestamp())
        self.currency = currency
        self.trade_mode = trade_mode
        self.symbol_name = symbol_name
        self.advance_ticks = advance_ticks
        self.init_ok = init_ok
        self.usd_zar_symbol = usd_zar_symbol
        self.maxbars = maxbars
        self.spec_warmup_reads = spec_warmup_reads
        self.no_history = no_history
        self.positions = positions
        self.equity = equity
        self.balance = balance
        self.data_path = data_path
        self.deals = list(deals) if deals else []
        self.deals_unavailable = deals_unavailable
        self.order_retcode = order_retcode
        self.order_retcodes = list(order_retcodes) if order_retcodes else None
        self.order_comment = order_comment
        self.order_raises = order_raises
        self.tick = tick
        self.tick_bid = tick_bid
        self.tick_ask = tick_ask
        self.terminal_trade_allowed = terminal_trade_allowed
        # Requests the adapter made, in order; the totals a test asserts on.
        self.orders: list[Any] = []
        self.extra_positions: list[Any] = []
        self._next_order = 500_000
        self.init_kwargs: dict[str, Any] = {}
        self.calls: list[str] = []
        self._tick_counter = 0
        self._spec_reads = 0
        self._last_error: tuple[int, str] = (1, "Success")

    def add_position(
        self,
        ticket: int,
        *,
        symbol: str | None = None,
        kind: int = 0,
        volume: float = 0.1,
        price_open: float = 150.1,
        sl: float = 0.0,
        tp: float = 0.0,
        magic: int = 0,
    ) -> Any:
        """A position the terminal reports as open, with a ticket a test chose."""
        row = SimpleNamespace(
            ticket=ticket,
            symbol=symbol or self.symbol_name,
            type=kind,
            volume=volume,
            price_open=price_open,
            price_current=price_open,
            sl=sl,
            tp=tp,
            profit=0.0,
            swap=0.0,
            time=self.now_epoch - 3600,
            magic=magic,
            comment="",
        )
        self.extra_positions.append(row)
        return row

    def initialize(self, **kwargs: Any) -> bool:
        self.calls.append("initialize")
        self.init_kwargs = kwargs
        return self.init_ok

    def shutdown(self) -> None:
        self.calls.append("shutdown")

    def last_error(self) -> tuple[int, str]:
        return self._last_error

    def terminal_info(self) -> Any:
        return SimpleNamespace(
            maxbars=self.maxbars,
            data_path=self.data_path,
            trade_allowed=self.terminal_trade_allowed,
        )

    def account_info(self) -> Any:
        return SimpleNamespace(
            login=12345678,
            server="HFMarketsSV-Demo",
            company="HFM",
            currency=self.currency,
            leverage=500,
            trade_mode=self.trade_mode,
            trade_allowed=False,
            margin_so_call=50.0,
            margin_so_so=20.0,
            balance=self.balance,
            equity=self.equity,
        )

    def positions_get(self, symbol: str | None = None) -> list[Any]:
        rows = [
            SimpleNamespace(
                ticket=9000 + index,
                symbol=self.symbol_name,
                type=0,
                volume=0.01,
                price_open=150.100,
                # Slightly behind the open, so a held row reads as a small drawdown rather than 0.
                price_current=150.100 - 0.05 * (index + 1),
                sl=0.0,
                tp=0.0,
                profit=-0.5,
                swap=0.0,
                time=self.now_epoch - 3600 * (index + 1),
                magic=0,
                comment="",
            )
            for index in range(self.positions)
        ]
        rows += list(self.extra_positions)
        if symbol is None:
            return rows
        # The real call takes a name pattern; here an exact match is what every caller wants.
        return [row for row in rows if row.symbol == symbol]

    def history_deals_get(self, date_from: Any, date_to: Any) -> list[Any] | None:
        """Every configured deal, ignoring the window: a test sets only the deals it cares about."""
        self.calls.append("history_deals_get")
        if self.deals_unavailable:
            self._last_error = INVALID_PARAMS
            return None
        self._last_error = (1, "Success")
        return list(self.deals)

    def symbols_get(self, group: str = "") -> list[Any]:
        names = [self.symbol_name, "EURUSDc"] if self.symbol_name else []
        if self.usd_zar_symbol:
            names.append("USDZAR")
        # The real call takes a group pattern like "*USD*"; honoured so a caller's own filter is
        # exercised rather than silently widened.
        return [SimpleNamespace(name=n) for n in names if fnmatch.fnmatch(n, group)]

    def symbol_info(self, name: str) -> Any:
        self._spec_reads += 1
        # The value of 0 the terminal answers right after waking: the first reads see it, later
        # ones see a real tick value, so a reader that caches on the first read is the bug.
        warming = self._spec_reads <= self.spec_warmup_reads
        return SimpleNamespace(
            visible=True,
            digits=3,
            point=0.001,
            trade_tick_size=0.001,
            trade_tick_value=0.0 if warming else 0.6667,
            trade_contract_size=1000.0,
            volume_min=0.01,
            volume_step=0.01,
            volume_max=100.0,
            trade_mode=4,
            currency_profit="JPY",
            swap_long=-1.2,
            swap_short=0.5,
            spread=14,
        )

    def symbol_select(self, name: str, enable: bool = True) -> bool:
        return True

    def order_send(self, request: dict[str, Any]) -> Any:
        """Record the request and answer as the terminal would: a result object, never a raise.

        A fake that raised on refusal would let the adapter's exception handling be the tested path,
        which is not how MetaTrader5 behaves - it answers with a retcode and the caller reads it.
        """
        self.calls.append("order_send")
        self.orders.append(dict(request))
        if self.order_raises:
            raise RuntimeError("the terminal is gone")

        # The management pass: move the stop/target of a position the test seeded.
        if request.get("action") == self.TRADE_ACTION_SLTP and not self.order_retcode:
            for row in self.extra_positions:
                if row.ticket == request.get("position"):
                    if request.get("sl") is not None:
                        row.sl = float(request["sl"])
                    if request.get("tp") is not None:
                        row.tp = float(request["tp"])

        self._next_order += 1
        if self.order_retcodes:
            retcode = self.order_retcodes.pop(0) if len(self.order_retcodes) > 1 else self.order_retcodes[0]
        else:
            retcode = self.order_retcode if self.order_retcode is not None else self.TRADE_RETCODE_DONE
        return SimpleNamespace(
            retcode=retcode,
            comment=self.order_comment,
            order=self._next_order,
            deal=self._next_order,
            volume=float(request.get("volume", 0.0)),
            price=float(request.get("price", 0.0)),
            request_id=1,
        )

    def symbol_info_tick(self, name: str) -> Any:
        if not self.tick:
            return None
        if self.advance_ticks:
            self._tick_counter += 1
        return SimpleNamespace(
            time=self.now_epoch + SERVER_OFFSET_S - 2,
            time_msc=(self.now_epoch + SERVER_OFFSET_S) * 1000 + self._tick_counter,
            bid=self.tick_bid,
            ask=self.tick_ask,
        )

    def copy_rates_from_pos(self, symbol: str, timeframe: int, start: int, count: int) -> Any:
        if self.no_history or count > self.maxbars:
            self._last_error = INVALID_PARAMS
            return None
        self._last_error = (1, "Success")
        interval = {self.TIMEFRAME_M1: 60, self.TIMEFRAME_M5: 300, self.TIMEFRAME_M15: 900,
                    self.TIMEFRAME_M30: 1800, self.TIMEFRAME_H1: 3600}[timeframe]
        span_s = 3 * 86_400
        end_utc = self.now_epoch - (self.now_epoch % interval)
        utc = np.arange(end_utc - span_s, end_utc, interval, dtype=np.int64)
        if interval == 60:  # two-hour data outage in the middle of the series
            utc = utc[(utc < end_utc - 40_000) | (utc >= end_utc - 40_000 + 7200)]
        rates = np.zeros(utc.size, dtype=RATE_DTYPE)
        rates["time"] = utc + SERVER_OFFSET_S
        rates["spread"] = 14
        # A deterministic, gentle wave around the tick's own price: enough structure that an
        # average true range is measurable, and far below the fast-move trip multiple.
        step = np.arange(utc.size, dtype="float64")
        open_ = 150.100 + 0.020 * np.sin(step / 7.0)
        close = 150.100 + 0.020 * np.sin((step + 1.0) / 7.0)
        rates["open"] = open_
        rates["close"] = close
        rates["high"] = np.maximum(open_, close) + 0.005
        rates["low"] = np.minimum(open_, close) - 0.005
        return rates[-count:][:count] if count > 0 else rates[:0]
