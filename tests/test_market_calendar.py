"""Tests for the market-calendar gate."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from trading_bot.market_calendar import StaticMarketCalendar, is_market_open

ET = ZoneInfo('America/New_York')
DAY = date(2026, 6, 2)


def test_regular_session_open_and_closed_times() -> None:
    cal = StaticMarketCalendar.regular([DAY])
    assert is_market_open(cal, datetime(2026, 6, 2, 10, 0, tzinfo=ET))  # mid-session
    assert not is_market_open(cal, datetime(2026, 6, 2, 8, 0, tzinfo=ET))  # pre-open
    assert not is_market_open(cal, datetime(2026, 6, 2, 16, 30, tzinfo=ET))  # after close


def test_holiday_is_closed_all_day() -> None:
    cal = StaticMarketCalendar.regular([DAY])  # only 6/2 is a session
    assert not is_market_open(cal, datetime(2026, 6, 3, 10, 0, tzinfo=ET))  # 6/3 not a session


def test_half_day_uses_early_close() -> None:
    cal = StaticMarketCalendar.regular([DAY], close_t=time(13, 0))
    assert is_market_open(cal, datetime(2026, 6, 2, 12, 0, tzinfo=ET))
    assert not is_market_open(cal, datetime(2026, 6, 2, 14, 0, tzinfo=ET))  # past early close


def test_always_open_calendar() -> None:
    cal = StaticMarketCalendar(always_open=True)
    assert is_market_open(cal, datetime(2026, 6, 6, 3, 0, tzinfo=ET))  # a Saturday, 3am


def test_naive_datetime_assumed_et() -> None:
    cal = StaticMarketCalendar.regular([DAY])
    assert is_market_open(cal, datetime(2026, 6, 2, 10, 0))  # naive -> treated as ET
