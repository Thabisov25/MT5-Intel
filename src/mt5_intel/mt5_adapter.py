"""Allow-listed, read-only view of the MetaTrader5 package (D-019, D-020).

Only the methods below are reachable. Order placement, modification and closing are not exposed,
and the raw backend is kept private.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import numpy as np

ACCOUNT_TRADE_MODES: dict[int, str] = {0: "DEMO", 1: "CONTEST", 2: "REAL"}
SYMBOL_TRADE_MODES: dict[int, str] = {
    0: "DISABLED",
    1: "LONG_ONLY",
    2: "SHORT_ONLY",
    3: "CLOSE_ONLY",
    4: "FULL",
}


class ReadOnlyMT5:
    def __init__(self, backend: Any) -> None:
        self.__backend = backend

    def connect(
        self,
        *,
        login: int,
        password: str,
        server: str,
        terminal_path: str | None,
        timeout_ms: int = 60_000,
    ) -> None:
        kwargs: dict[str, Any] = {
            "login": login,
            "password": password,
            "server": server,
            "timeout": timeout_ms,
        }
        if terminal_path:
            kwargs["path"] = terminal_path
        if not self.__backend.initialize(**kwargs):
            # The error tuple carries no credentials.
            raise ConnectionError(f"MT5 initialize failed: {self.last_error()}")

    def shutdown(self) -> None:
        self.__backend.shutdown()

    def last_error(self) -> tuple[int, str]:
        code, message = self.__backend.last_error()
        return int(code), str(message)

    def account_info(self) -> Any:
        return self.__backend.account_info()

    def positions_get(self, symbol: str | None = None) -> tuple[Any, ...]:
        """Open positions, read-only. The gate needs the count; nothing here can change one."""
        rows = self.__backend.positions_get() if symbol is None else self.__backend.positions_get(symbol=symbol)
        return tuple(rows or ())

    def history_deals_get(self, from_epoch: int, to_epoch: int) -> tuple[Any, ...]:
        """Closed-deal history between two instants, read-only.

        This is how the trade journal turns a position that has disappeared from the terminal
        into a finished trade: the exit price, the exit time, and what the deals actually made
        once spread and commission are charged. The allow-list is unchanged (D-019) - a deal
        history read cannot place, modify or close anything.
        """
        start = datetime.fromtimestamp(int(from_epoch), tz=UTC)
        end = datetime.fromtimestamp(int(to_epoch), tz=UTC)
        rows = self.__backend.history_deals_get(start, end)
        return tuple(rows or ())

    def symbols_get(self, group: str) -> tuple[Any, ...]:
        return tuple(self.__backend.symbols_get(group=group) or ())

    def symbol_info(self, name: str) -> Any:
        return self.__backend.symbol_info(name)

    def symbol_select(self, name: str, enable: bool = True) -> bool:
        return bool(self.__backend.symbol_select(name, enable))

    def symbol_info_tick(self, name: str) -> Any:
        return self.__backend.symbol_info_tick(name)

    def terminal_max_bars(self) -> int | None:
        info = self.__backend.terminal_info()
        maxbars = int(getattr(info, "maxbars", 0) or 0) if info is not None else 0
        return maxbars if maxbars > 0 else None

    def terminal_data_path(self) -> str | None:
        """The terminal's data folder, so exports can reach its MQL5\\Files directory.

        Read-only in the sense that matters here (D-019): it is a path string, and nothing in this
        adapter can place, modify or close an order.
        """
        info = self.__backend.terminal_info()
        path = getattr(info, "data_path", None) if info is not None else None
        return str(path) if path else None

    def copy_rates_from_pos(self, symbol: str, timeframe: str, start: int, count: int) -> np.ndarray:
        rates = self.__backend.copy_rates_from_pos(
            symbol, self.__timeframe(timeframe), start, count
        )
        if rates is None:
            return np.zeros(0, dtype=[("time", "<i8"), ("spread", "<i4")])
        return np.asarray(rates)

    def __timeframe(self, name: str) -> int:
        if name not in ("M1", "M5", "M15", "M30", "H1", "H4", "D1"):
            raise ValueError(f"timeframe {name} is not allow-listed")
        return int(getattr(self.__backend, f"TIMEFRAME_{name}"))
