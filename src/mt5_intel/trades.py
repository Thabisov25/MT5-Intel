"""The trade journal: what the account actually did, priced, timed and attributed.

The gate decides whether a card may be taken, and the setups view says which strategies have a
card running right now. Neither answers the question a trader asks after the fact: *which trades
did I take, which strategy was I following, and what did they make?* This module reads that from
the terminal itself - the open positions and the closed deals - so nothing has to be typed in and
a trade that has been forgotten still shows up.

Read-only, like everything else that touches the terminal (D-019): positions and deal history are
reads, and the adapter still exposes no way to place, modify or close an order.

Two facts are recorded per trade:

* when it was taken and when it closed, from the terminal's own stamps;
* which strategies had a live card in the setups view when the position was first seen. That is
  the "this and this strategy was live" line - a snapshot rather than a guess, taken from the same
  bars the rules run on.

Which strategy the trader was actually *following* is theirs to say and is deliberately not
inferred here: the page records it as a label (see the dashboard's trade labels). This module only
reports what the account did, and what was on offer when it did it.

The artifact is rebuilt from the terminal rather than accumulated in the journal, because MT5's
own deal history already is the durable record: a broker keeps a closed trade forever, so a second
copy could only drift from it. The one thing that cannot be re-derived is the context snapshot, so
an open trade carries its own context forward from the previous artifact.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SIDE_BUY = "buy"
SIDE_SELL = "sell"
STATUS_OPEN = "open"
STATUS_CLOSED = "closed"

# The terminal's own vocabulary, named so the comparisons read as what they mean.
DEAL_TYPE_BUY = 0
DEAL_ENTRY_OUT = 1

# How far back closed trades are read. A broker keeps deals forever, but a journal does not need
# years of them re-read every minute: a trade older than this is still in the terminal and simply
# not on the page.
LOOKBACK_DAYS = 180
# A day of slack past "now" on the closing edge of that window. Deal stamps are the broker's
# clock, which runs ahead of UTC, so a trade closed in the last hours of the window would fall
# outside a window that ended at UTC now.
WINDOW_SLACK_S = 86_400
# The page is a journal, not an archive: the newest rows, open ones first.
MAX_ROWS = 400


def fetch_window(now_epoch: int, days: int = LOOKBACK_DAYS) -> tuple[int, int]:
    """The ``(from, to)`` instants to ask the terminal for closed deals over."""
    return (int(now_epoch) - days * 86_400, int(now_epoch) + WINDOW_SLACK_S)


def load_artifact(path: Path) -> dict[str, Any] | None:
    """Read a JSON artifact another process writes, tolerating a missing or half-written one."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _field(row: Any, name: str) -> Any:
    """A terminal row's field, by attribute or by key. A missing field reads as None."""
    value = getattr(row, name, None)
    if value is None and isinstance(row, Mapping):
        value = row.get(name)
    return value


def _num(value: Any) -> float | None:
    """A finite number, or None. A NaN or an unparseable cell is not a number the page can show."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int(value: Any) -> int | None:
    number = _num(value)
    return None if number is None else int(number)


def _stamp(epoch: Any, offset_s: int | None) -> str | None:
    """A terminal epoch as ``YYYY-MM-DD HH:MM``, on the UTC clock when the broker's is known.

    Trade stamps carry the broker's clock exactly as bars do (D-011), so the offset is subtracted
    when there is one: a trade taken at 09:00 server time is not a trade taken at 09:00 UTC, and
    the page ages its rows against the reader's own clock.
    """
    value = _int(epoch)
    if value is None:
        return None
    instant = value - (offset_s or 0)
    return datetime.fromtimestamp(instant, UTC).strftime("%Y-%m-%d %H:%M")


def _side(deal_type: Any) -> str | None:
    kind = _int(deal_type)
    if kind is None:
        return None
    return SIDE_BUY if kind == DEAL_TYPE_BUY else SIDE_SELL


def is_watched(symbol: str, watched: str) -> bool:
    """Does a position's symbol belong to the symbol the setups view covers?

    A broker puts its own suffix on its symbols (USDJPYc, USDJPY.r, USDJPYm) while the setups
    view is built for the configured root. Without this, no card's context would ever attach to a
    trade taken on the suffixed symbol - which is the symbol the trader actually trades.
    """
    if not symbol or not watched:
        return False
    left, right = symbol.lower(), watched.lower()
    return left == right or (left.startswith(right) and len(left) - len(right) <= 4)


def _weighted(pairs: list[tuple[float, float]]) -> float | None:
    """Volume-weighted price, so a position filled in pieces reports the price it really got."""
    total = sum(volume for volume, _ in pairs)
    if total <= 0:
        return None
    return sum(volume * price for volume, price in pairs) / total


class _Bucket:
    """Everything the deals of one position add up to."""

    __slots__ = (
        "closed_epoch",
        "comment",
        "commission",
        "deals",
        "entries",
        "entry_side",
        "exit_side",
        "exits",
        "magic",
        "net",
        "opened_epoch",
        "profit",
        "swap",
        "symbol",
    )

    def __init__(self) -> None:
        self.entries: list[tuple[float, float]] = []
        self.exits: list[tuple[float, float]] = []
        self.profit = 0.0
        self.swap = 0.0
        self.commission = 0.0
        self.net = 0.0
        self.deals = 0
        self.symbol: str | None = None
        self.opened_epoch: int | None = None
        self.closed_epoch: int | None = None
        self.entry_side: str | None = None
        self.exit_side: str | None = None
        self.comment: str | None = None
        self.magic: int | None = None


def index_deals(deals: Any) -> dict[int, _Bucket]:
    """Group deals by the position they belong to: the entry, the exits, and what they cost.

    A position can be filled and closed across several deals, so one row per deal would show one
    trade as many. Cost is summed here (profit + swap + commission) rather than taken from the
    closing deal alone: commission is charged on the entry deal for a market order, and each
    partial close leaves its own profit behind on the way out.
    """
    by_position: dict[int, _Bucket] = {}
    for deal in deals or ():
        position = _int(_field(deal, "position_id"))
        if position is None:
            position = _int(_field(deal, "position"))
        if position is None:
            continue
        bucket = by_position.setdefault(position, _Bucket())
        bucket.deals += 1
        profit = _num(_field(deal, "profit")) or 0.0
        swap = _num(_field(deal, "swap")) or 0.0
        commission = _num(_field(deal, "commission")) or 0.0
        bucket.profit += profit
        bucket.swap += swap
        bucket.commission += commission
        bucket.net += profit + swap + commission
        symbol = _field(deal, "symbol")
        if bucket.symbol is None and isinstance(symbol, str) and symbol:
            bucket.symbol = symbol
        volume = _num(_field(deal, "volume")) or 0.0
        price = _num(_field(deal, "price"))
        epoch = _int(_field(deal, "time"))
        side = _side(_field(deal, "type"))
        if _int(_field(deal, "entry")) == DEAL_ENTRY_OUT:
            if price is not None:
                bucket.exits.append((volume, price))
            if epoch is not None:
                bucket.closed_epoch = epoch if bucket.closed_epoch is None else max(bucket.closed_epoch, epoch)
            if bucket.exit_side is None:
                bucket.exit_side = side
        else:
            if price is not None:
                bucket.entries.append((volume, price))
            if epoch is not None:
                bucket.opened_epoch = epoch if bucket.opened_epoch is None else min(bucket.opened_epoch, epoch)
            if bucket.entry_side is None:
                bucket.entry_side = side
            comment = _field(deal, "comment")
            if bucket.comment is None and isinstance(comment, str) and comment:
                bucket.comment = comment
            magic = _int(_field(deal, "magic"))
            if bucket.magic is None and magic is not None:
                bucket.magic = magic
    return by_position


def live_context(setups: dict[str, Any] | None) -> tuple[list[dict[str, Any]], str | None]:
    """The strategies with a card running in the setups view, and when that view was taken.

    This is the "which strategies were live" half of a trade's record. It is read from the same
    artifact the Live page shows, so what a trade captured as context is exactly what the trader
    could see at the time, not a reconstruction.
    """
    if not setups:
        return [], None
    rows: list[dict[str, Any]] = []
    raw_rows = setups.get("setups")
    for row in raw_rows if isinstance(raw_rows, list) else []:
        if not isinstance(row, Mapping) or row.get("status") != STATUS_OPEN:
            continue
        rows.append(
            {
                "family": row.get("family"),
                "params": row.get("params"),
                "side": row.get("side"),
                "entry": row.get("entry"),
                "sl": row.get("sl"),
                "tp": row.get("tp"),
                "signal_utc": row.get("signal_utc"),
                "r_now": row.get("r_now"),
            }
        )
    asof = setups.get("asof_bar_utc")
    return rows, asof if isinstance(asof, str) else None


def _prior_rows(previous: dict[str, Any] | None) -> dict[int, Mapping[str, Any]]:
    """The previous artifact's rows by ticket, so an open trade keeps what was captured for it."""
    if not previous:
        return {}
    rows = previous.get("trades")
    out: dict[int, Mapping[str, Any]] = {}
    for row in rows if isinstance(rows, list) else []:
        ticket = _int(row.get("ticket")) if isinstance(row, Mapping) else None
        if ticket is not None:
            out[ticket] = row
    return out


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, max(0, min(8, digits)))


def _open_row(
    position: Any,
    bucket: _Bucket | None,
    prior: Mapping[str, Any] | None,
    new_context: list[dict[str, Any]],
    context_asof: str | None,
    watched_symbol: str,
    offset_s: int | None,
    digits: int,
    now_epoch: int,
) -> dict[str, Any]:
    """A position the account is still in, marked to the terminal's own floating profit.

    Net is the floating result plus whatever the deals already show (the entry commission, and the
    realised part of a position that has been partly closed), so the number the page prints is the
    one the terminal's account column would show, not a second estimate of it.
    """
    symbol = str(_field(position, "symbol") or "")
    profit = _num(_field(position, "profit")) or 0.0
    swap = _num(_field(position, "swap")) or 0.0
    booked = bucket.net if bucket is not None else 0.0
    # A trade keeps the context captured when it was first seen - the view on offer at the time it
    # was taken - rather than the one on offer now, hours into the trade.
    if prior is not None:
        context = prior.get("context")
        context_utc = prior.get("context_utc")
        captured = prior.get("context_captured_utc")
    else:
        watched = is_watched(symbol, watched_symbol)
        context = new_context if watched else []
        context_utc = context_asof if watched else None
        captured = _stamp(now_epoch, 0)
    comment = _field(position, "comment") or (bucket.comment if bucket is not None else None) or None
    magic = _int(_field(position, "magic"))
    if magic is None and bucket is not None:
        magic = bucket.magic
    return {
        "ticket": _int(_field(position, "ticket")),
        "symbol": symbol,
        "digits": digits,
        "side": _side(_field(position, "type")),
        "volume": round(_num(_field(position, "volume")) or 0.0, 2),
        "status": STATUS_OPEN,
        "entry": _round(_num(_field(position, "price_open")), digits),
        "sl": _round(_num(_field(position, "sl")), digits),
        "tp": _round(_num(_field(position, "tp")), digits),
        "price_now": _round(_num(_field(position, "price_current")), digits),
        "profit": round(profit, 2),
        "swap": round(swap, 2),
        "commission": round(bucket.commission if bucket is not None else 0.0, 2),
        "net": round(profit + swap + booked, 2),
        "opened_epoch": _int(_field(position, "time")),
        "opened_utc": _stamp(_field(position, "time"), offset_s),
        "closed_epoch": None,
        "closed_utc": None,
        "exit": None,
        "comment": comment,
        "magic": magic,
        "deals": bucket.deals if bucket is not None else 0,
        "context": context if isinstance(context, list) else [],
        "context_utc": context_utc if isinstance(context_utc, str) else None,
        "context_captured_utc": captured if isinstance(captured, str) else None,
    }


def _closed_row(
    position: int,
    bucket: _Bucket,
    offset_s: int | None,
    digits: int,
    prior: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """A position that has gone from the terminal, rebuilt from the deals that closed it.

    The entry price can only come from the opening deal, and a position opened before the window
    the terminal was asked for has none: the row leaves the entry empty rather than reporting the
    exit price as if it had been the entry. Everything else - side, volume, exit, times and the
    net result - comes from the deals themselves.
    """
    side = bucket.entry_side
    if side is None and bucket.exit_side is not None:
        # No entry deal in the window: the closing deal's type names the side it closed, which is
        # the position's opposite.
        side = SIDE_SELL if bucket.exit_side == SIDE_BUY else SIDE_BUY
    exit_volume = sum(volume for volume, _ in bucket.exits)
    entry_volume = sum(volume for volume, _ in bucket.entries)
    return {
        "ticket": position,
        "symbol": bucket.symbol or "",
        "digits": digits,
        "side": side,
        "volume": round(exit_volume or entry_volume, 2),
        "status": STATUS_CLOSED,
        "entry": _round(_weighted(bucket.entries), digits),
        "sl": None,
        "tp": None,
        "price_now": None,
        "profit": round(bucket.profit, 2),
        "swap": round(bucket.swap, 2),
        "commission": round(bucket.commission, 2),
        "net": round(bucket.net, 2),
        "opened_epoch": bucket.opened_epoch,
        "opened_utc": _stamp(bucket.opened_epoch, offset_s),
        "closed_epoch": bucket.closed_epoch,
        "closed_utc": _stamp(bucket.closed_epoch, offset_s),
        "exit": _round(_weighted(bucket.exits), digits),
        "comment": (prior.get("comment") if prior is not None else None) or bucket.comment,
        "magic": prior.get("magic") if (prior is not None and prior.get("magic") is not None) else bucket.magic,
        "deals": bucket.deals,
        # A closed trade keeps the context captured while it was open, so a trade taken on a live
        # card still says which card it was after it has finished.
        "context": prior.get("context") if prior is not None and isinstance(prior.get("context"), list) else [],
        "context_utc": prior.get("context_utc") if prior is not None else None,
        "context_captured_utc": prior.get("context_captured_utc") if prior is not None else None,
    }


def build(
    *,
    positions: Any,
    deals: Any,
    previous: dict[str, Any] | None = None,
    setups: dict[str, Any] | None = None,
    watched_symbol: str = "",
    digits_by_symbol: Mapping[str, int] | None = None,
    default_digits: int = 5,
    offset_s: int | None = None,
    account: int | None = None,
    currency: str | None = None,
    now_epoch: int,
    max_rows: int = MAX_ROWS,
) -> dict[str, Any]:
    """The whole trade view: the account's open positions, then its closed trades.

    Prices are quoted as the symbol quotes them, and the money columns are the account's own
    currency exactly as the terminal reports it, so no conversion is invented here.
    """
    digit_lookup = digits_by_symbol or {}
    prior = _prior_rows(previous)
    context, context_asof = live_context(setups)
    index = index_deals(deals)

    # A position with no ticket cannot be matched to its deals or its previous row, but it is
    # still a trade the account is in: it is shown, it just cannot carry a context forward.
    open_rows: list[dict[str, Any]] = []
    open_tickets: set[int] = set()
    for position in positions or ():
        ticket = _int(_field(position, "ticket"))
        symbol = str(_field(position, "symbol") or "")
        if ticket is not None:
            open_tickets.add(ticket)
        open_rows.append(
            _open_row(
                position,
                index.get(ticket) if ticket is not None else None,
                prior.get(ticket) if ticket is not None else None,
                context,
                context_asof,
                watched_symbol,
                offset_s,
                int(digit_lookup.get(symbol, default_digits)),
                now_epoch,
            )
        )
    open_rows.sort(key=lambda row: row["opened_epoch"] or 0, reverse=True)

    closed: list[dict[str, Any]] = []
    for position, bucket in index.items():
        if position in open_tickets or not bucket.exits:
            # Still open - the position row above is the truth for it, partial closes included - or
            # an opening deal with nothing closing it yet.
            continue
        symbol = bucket.symbol or ""
        closed.append(
            _closed_row(
                position, bucket, offset_s, int(digit_lookup.get(symbol, default_digits)), prior.get(position)
            )
        )
    closed.sort(key=lambda row: (row["closed_epoch"] or 0, row["ticket"] or 0), reverse=True)

    kept_closed = closed[: max(0, max_rows - len(open_rows))]
    net_open = round(sum(row["net"] for row in open_rows), 2)
    net_closed = round(sum(row["net"] for row in kept_closed), 2)
    wins = sum(1 for row in kept_closed if row["net"] > 0)
    losses = sum(1 for row in kept_closed if row["net"] < 0)
    return {
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "clock": "utc" if offset_s is not None else "server",
        "symbol": watched_symbol,
        "account": account,
        "currency": currency,
        "lookback_days": LOOKBACK_DAYS,
        "context_asof_utc": context_asof,
        "open_count": len(open_rows),
        "open_net": net_open,
        "closed_count": len(kept_closed),
        "closed_net": net_closed,
        "total_net": round(net_open + net_closed, 2),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / (wins + losses), 3) if wins + losses else None,
        "trades": open_rows + kept_closed,
    }
