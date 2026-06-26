"""Tests for the parallel data-gathering graph (uses a static provider, no network)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from trading_bot.domain.market_state import (
    EconomicEvent,
    Indicators,
    MarketContext,
    NewsItem,
)
from trading_bot.reasoning.graph import gather_market_state
from trading_bot.reasoning.providers import StaticMarketDataProvider

ET = ZoneInfo('America/New_York')
AS_OF = datetime(2026, 6, 2, 10, 0, tzinfo=ET)


def test_gather_assembles_market_state_from_all_sources() -> None:
    provider = StaticMarketDataProvider(
        {'QQQ': Indicators(symbol='QQQ', price=400.0)},
        context=MarketContext(vix=15.0, regime='risk_on'),
        news=(NewsItem(timestamp=AS_OF - timedelta(minutes=5), headline='past headline'),),
        events=(EconomicEvent(timestamp=AS_OF + timedelta(hours=2), name='CPI', impact='high'),),
    )
    state = gather_market_state(provider, AS_OF, equity=100_000.0)

    assert state.as_of == AS_OF
    assert state.equity == 100_000.0
    assert 'QQQ' in state.indicators
    assert state.context.vix == 15.0
    assert len(state.news) == 1
    assert state.events[0].name == 'CPI'


def test_gather_filters_future_news() -> None:
    provider = StaticMarketDataProvider(
        {'QQQ': Indicators(symbol='QQQ', price=400.0)},
        news=(
            NewsItem(timestamp=AS_OF - timedelta(minutes=1), headline='past'),
            NewsItem(timestamp=AS_OF + timedelta(minutes=1), headline='future — must be dropped'),
        ),
    )
    state = gather_market_state(provider, AS_OF, equity=50_000.0)
    headlines = [n.headline for n in state.news]
    assert headlines == ['past']
