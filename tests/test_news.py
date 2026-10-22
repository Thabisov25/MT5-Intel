from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from mt5_intel.news import (
    NewsEvent,
    NewsUnavailable,
    _parse_events,
    fetch_and_cache,
    next_event,
    within_news_window,
)


def _event(title: str, currency: str, impact: str, minutes_from_now: int) -> dict[str, Any]:
    when = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=minutes_from_now)
    return {"title": title, "country": currency, "impact": impact, "date": when.isoformat()}


def test_parse_filters_to_usd_jpy_high_only() -> None:
    rows = [
        _event("NFP", "USD", "High", 60),
        _event("CPI", "JPY", "High", 120),
        _event("Retail", "GBP", "High", 90),      # wrong currency
        _event("BoJ", "JPY", "Low", 30),          # wrong impact
        _event("Bad date", "USD", "High", 0),     # wrong timestamp gets dropped
    ]
    rows[-1]["date"] = "not-a-date"
    events = _parse_events(rows)
    titles = [e.title for e in events]
    assert titles == ["NFP", "CPI"]


def test_window_math_thirty_minutes() -> None:
    when = int(datetime.now(UTC).timestamp())
    event = NewsEvent("NFP", "USD", "High", when + 29 * 60)
    assert within_news_window([event], when) is True
    far = NewsEvent("NFP", "USD", "High", when + 45 * 60)
    assert within_news_window([far], when) is False


def test_next_event_returns_soonest_upcoming() -> None:
    now = int(datetime.now(UTC).timestamp())
    later = NewsEvent("BoJ", "JPY", "High", now + 3600)
    sooner = NewsEvent("ISM", "USD", "High", now + 600)
    picked = next_event([later, sooner], now)
    assert picked is not None and picked is sooner


def test_fetch_uses_fresh_cache_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "news_cache.json"
    now = datetime.now(UTC)
    events = [NewsEvent("NFP", "USD", "High", int((now + timedelta(hours=1)).timestamp()))]
    cache.write_text(
        json.dumps({"fetched_at": now.isoformat(timespec="seconds"),
                    "events": [e.to_payload() for e in events]}),
        encoding="utf-8",
    )

    def fail_fetch(*_a: object) -> None:
        raise AssertionError("network must not be touched when cache is fresh")

    monkeypatch.setattr("mt5_intel.news.urllib.request.urlopen", fail_fetch)
    loaded, fetched = fetch_and_cache(cache, now)
    assert fetched is False
    assert loaded[0].title == "NFP"


def test_offline_with_no_cache_raises_news_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "news_cache.json"

    class Boom(OSError):
        pass

    def fail_fetch(*_a: object, **_k: object) -> None:
        raise Boom("offline")

    monkeypatch.setattr("mt5_intel.news.urllib.request.urlopen", fail_fetch)
    with pytest.raises(NewsUnavailable):
        fetch_and_cache(cache)
