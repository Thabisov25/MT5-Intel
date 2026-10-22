"""Trading sessions, defined on each centre's own clock (D-011) and resolved from UTC.

The system keeps its data in UTC (D-024), but a session is a local-clock fact: London opens at
08:00 *in London*, which is 08:00 UTC in winter and 07:00 UTC in summer. Hard-coded UTC windows
would therefore mislabel roughly half the year, which is exactly why D-011 defines them on the
local clocks. The conversion goes through the IANA database; when that database is missing the
resolver raises rather than guessing, because a guessed session label is a fabricated fact.

This module is pure: epoch seconds in, labels out. Nothing here reads the journal or the clock.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Conventional local business window of each FX centre. Tokyo does not observe DST, so its UTC
# window is stable; London and New York move, which is the point of resolving them here.
#   Tokyo 09:00-18:00 JST        -> 00:00-09:00 UTC, all year
#   London 08:00-17:00 local     -> 08:00-17:00 UTC in winter, 07:00-16:00 UTC in summer
#   New York 08:00-17:00 local   -> 13:00-22:00 UTC in winter, 12:00-21:00 UTC in summer
SESSION_WINDOWS: tuple[tuple[str, str, time, time], ...] = (
    ("Tokyo", "Asia/Tokyo", time(9, 0), time(18, 0)),
    ("London", "Europe/London", time(8, 0), time(17, 0)),
    ("New York", "America/New_York", time(8, 0), time(17, 0)),
)

# Canonical labels, for ordering a table the same way twice.
OFF_HOURS = "off-hours"
SESSION_ORDER: tuple[str, ...] = tuple(name for name, *_ in SESSION_WINDOWS)


class SessionsUnavailable(RuntimeError):
    """The IANA time-zone database is missing, so local clocks cannot be resolved."""


def open_sessions(epoch_utc: int) -> tuple[str, ...]:
    """Centres whose local session is open at this UTC instant, in canonical order.

    Raises SessionsUnavailable when the time-zone database cannot be read, so a caller can
    report the gap instead of labelling the instant with a session it did not verify.
    """
    moment = datetime.fromtimestamp(int(epoch_utc), tz=UTC)
    open_now: list[str] = []
    for name, zone_name, start, end in SESSION_WINDOWS:
        try:
            zone = ZoneInfo(zone_name)
        except ZoneInfoNotFoundError as exc:  # pragma: no cover - environment, not logic
            raise SessionsUnavailable(
                f"time zone {zone_name} is unavailable; install the `tzdata` package"
            ) from exc
        local = moment.astimezone(zone).time()
        if start <= local < end:
            open_now.append(name)
    return tuple(open_now)


def session_label(epoch_utc: int) -> str:
    """A single bucket name for the instant, e.g. ``London + New York`` or ``off-hours``.

    Overlaps are their own bucket rather than being counted once per centre: their spreads and
    volatility are a different regime from either centre alone, and a dual count would make the
    per-session rates sum to more than 100% of cycles.
    """
    names = open_sessions(epoch_utc)
    return " + ".join(names) if names else OFF_HOURS
