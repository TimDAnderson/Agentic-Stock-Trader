"""Assemble a MarketState snapshot from gathered pieces (DECISIONS.md §6, §8).

Freshness guard: news is filtered to ``<= as_of`` here (and MarketState's own
validator enforces it), so a snapshot can never carry future news.
"""

from __future__ import annotations

from datetime import datetime

from trading_bot.domain.market_state import (
    EconomicEvent,
    Indicators,
    MarketContext,
    MarketState,
    NewsItem,
)


def assemble_market_state(
    as_of: datetime,
    equity: float,
    *,
    indicators: dict[str, Indicators],
    context: MarketContext,
    news: tuple[NewsItem, ...],
    events: tuple[EconomicEvent, ...],
) -> MarketState:
    fresh_news = tuple(item for item in news if item.timestamp <= as_of)
    return MarketState(
        as_of=as_of,
        indicators=indicators,
        equity=equity,
        context=context,
        news=fresh_news,
        events=events,
    )
