"""Session resolution must follow each centre's local clock, not a fixed UTC window."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfoNotFoundError

import pytest

from mt5_intel import sessions


def _epoch(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())


def test_utc_instant_resolves_to_each_centres_local_window() -> None:
    # 08:30 UTC in January is 17:30 JST (open), 08:30 GMT (open) and 03:30 EST (closed).
    assert sessions.open_sessions(_epoch("2026-01-15T08:30:00+00:00")) == ("Tokyo", "London")


def test_london_window_moves_across_the_dst_switch() -> None:
    # The same clock time on either side of the switch: 07:30 UTC is before London's 08:00 open
    # in winter, and 08:30 BST in summer.
    assert sessions.session_label(_epoch("2026-01-15T07:30:00+00:00")) == "Tokyo"
    assert sessions.session_label(_epoch("2026-07-15T07:30:00+00:00")) == "Tokyo + London"


def test_new_york_window_moves_across_the_dst_switch() -> None:
    # 12:30 UTC is 07:30 EST (closed) in winter and 08:30 EDT (open) in summer.
    assert "New York" not in sessions.open_sessions(_epoch("2026-01-15T12:30:00+00:00"))
    assert "New York" in sessions.open_sessions(_epoch("2026-07-15T12:30:00+00:00"))


def test_tokyo_has_no_dst_so_its_utc_window_is_stable() -> None:
    assert sessions.open_sessions(_epoch("2026-01-15T02:00:00+00:00")) == ("Tokyo",)
    assert sessions.open_sessions(_epoch("2026-07-15T02:00:00+00:00")) == ("Tokyo",)


def test_quiet_hours_are_their_own_bucket() -> None:
    assert sessions.session_label(_epoch("2026-01-15T22:30:00+00:00")) == sessions.OFF_HOURS


def test_overlap_is_reported_in_canonical_order() -> None:
    # 14:00 UTC in January: London 14:00 GMT and New York 09:00 EST are both open.
    assert sessions.open_sessions(_epoch("2026-01-15T14:00:00+00:00")) == ("London", "New York")


def test_resolver_answers_about_clocks_not_about_the_market_being_open() -> None:
    # A Saturday still resolves; the watcher journals no cycle then, so this never reaches a rate.
    assert sessions.session_label(_epoch("2026-01-17T14:00:00+00:00")) == "London + New York"


def test_missing_timezone_database_raises_instead_of_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_key: str) -> None:
        raise ZoneInfoNotFoundError("no tz database")

    monkeypatch.setattr(sessions, "ZoneInfo", boom)
    with pytest.raises(sessions.SessionsUnavailable, match="tzdata"):
        sessions.open_sessions(0)
