"""Economic news filter (D-032): public ForexFactory weekly JSON, no API key required.

Fetches once per refresh interval, caches to disk, exposes only USD and JPY high-impact events,
and answers one question: is a given UTC time inside a ±30-minute exclusion window around an event?

Fail-safe per D-027: if the feed is unreachable and no usable cache exists, NewsUnavailable is
raised and the caller must treat the news filter as NOT VERIFIED (recommendations suppressed),
never warn-and-continue.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FEED_HEADER = {"User-Agent": "mt5-intel/0.1 research (read-only)"}
CURRENCIES = {"USD", "JPY"}
IMPACTS = {"High"}
WINDOW_MINUTES = 30
REFRESH_MINUTES = 240  # weekly feed; refetch every 4h


class NewsUnavailable(RuntimeError):
    """Raised when the news filter cannot be verified (feed down, no usable cache)."""


@dataclass(frozen=True)
class NewsEvent:
    title: str
    currency: str
    impact: str
    epoch_utc: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "currency": self.currency,
            "impact": self.impact,
            "epoch_utc": self.epoch_utc,
        }


def _parse_events(raw: list[dict[str, Any]]) -> list[NewsEvent]:
    out: list[NewsEvent] = []
    for row in raw:
        currency = str(row.get("country", "")).upper()
        impact = str(row.get("impact", "")).title()
        if currency not in CURRENCIES or impact not in IMPACTS:
            continue
        try:
            when = datetime.fromisoformat(str(row.get("date", "")))
        except ValueError:
            continue  # unparseable timestamp is not an event we can trust
        out.append(
            NewsEvent(
                title=str(row.get("title", "unknown")),
                currency=currency,
                impact=impact,
                epoch_utc=int(when.timestamp()),
            )
        )
    out.sort(key=lambda e: e.epoch_utc)
    return out


def _events_from_cache(cached: dict[str, Any]) -> list[NewsEvent]:
    return [
        NewsEvent(
            title=str(e["title"]),
            currency=str(e["currency"]),
            impact=str(e["impact"]),
            epoch_utc=int(e["epoch_utc"]),
        )
        for e in cached.get("events", [])
    ]


def _cached_events_if_fresh(cache_path: Path) -> list[NewsEvent] | None:
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(str(cached["fetched_at"]))
        if datetime.now(UTC) - fetched_at < timedelta(minutes=REFRESH_MINUTES):
            return _events_from_cache(cached)
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        pass
    return None


def _cached_events_any_age(cache_path: Path) -> list[NewsEvent] | None:
    try:
        return _events_from_cache(json.loads(cache_path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return None


def fetch_and_cache(cache_path: Path, now: datetime | None = None) -> tuple[list[NewsEvent], bool]:
    """Return (filtered events, fetched_now_ok). Falls back to stale cache when offline."""
    now = now or datetime.now(UTC)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    fresh = _cached_events_if_fresh(cache_path)
    if fresh is not None:
        return fresh, False
    try:
        request = urllib.request.Request(FEED_URL, headers=FEED_HEADER)
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        events = _parse_events(payload if isinstance(payload, list) else [])
        cache_path.write_text(
            json.dumps({"fetched_at": now.isoformat(timespec="seconds"),
                        "events": [e.to_payload() for e in events]}),
            encoding="utf-8",
        )
        return events, True
    except (OSError, json.JSONDecodeError):
        stale = _cached_events_any_age(cache_path)
        if stale is not None:
            return stale, False
        raise NewsUnavailable("news feed unreachable and no usable cache") from None


def within_news_window(
    events: list[NewsEvent], now_epoch: int, window_minutes: int = WINDOW_MINUTES
) -> bool:
    """True when now is within ±window around any filtered (USD/JPY High) event."""
    half = window_minutes * 60
    return any(abs(event.epoch_utc - now_epoch) <= half for event in events)


def next_event(events: list[NewsEvent], now_epoch: int) -> NewsEvent | None:
    """Soonest event at or after now (events may arrive in any order)."""
    upcoming = [e for e in events if e.epoch_utc >= now_epoch]
    return min(upcoming, key=lambda e: e.epoch_utc) if upcoming else None
