"""Market-data provider abstraction (DECISIONS.md §8).

One Protocol with four independent fetches (indicators / context / news /
events) that the gather graph fans out over. Code computes the indicator values
upstream; providers just deliver clean, timestamped data. The real
Alpaca/news/economic-calendar implementations are part of the data-tools work;
``StaticMarketDataProvider`` is the network-free fake for tests and local runs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from trading_bot.domain.market_state import (
    EconomicEvent,
    Indicators,
    MarketContext,
    NewsItem,
)


@runtime_checkable
class MarketDataProvider(Protocol):
    def indicators(self, as_of: datetime) -> dict[str, Indicators]: ...

    def context(self, as_of: datetime) -> MarketContext: ...

    def news(self, as_of: datetime) -> tuple[NewsItem, ...]: ...

    def events(self, as_of: datetime) -> tuple[EconomicEvent, ...]: ...


class StaticMarketDataProvider:
    """Returns fixed data regardless of ``as_of`` — for tests and offline runs."""

    def __init__(
        self,
        indicators: dict[str, Indicators],
        *,
        context: MarketContext | None = None,
        news: tuple[NewsItem, ...] = (),
        events: tuple[EconomicEvent, ...] = (),
    ) -> None:
        self._indicators = indicators
        self._context = context or MarketContext()
        self._news = news
        self._events = events

    def indicators(self, as_of: datetime) -> dict[str, Indicators]:
        return self._indicators

    def context(self, as_of: datetime) -> MarketContext:
        return self._context

    def news(self, as_of: datetime) -> tuple[NewsItem, ...]:
        return self._news

    def events(self, as_of: datetime) -> tuple[EconomicEvent, ...]:
        return self._events
