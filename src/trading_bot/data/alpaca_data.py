"""Alpaca-backed market-data provider (DECISIONS.md §8).

Fetches minute bars and runs the **same** ``compute_indicators`` the backtester
uses — so the indicator values are identical to what was tested. alpaca-py's
data client is imported lazily (broker extra), so importing this module never
requires the SDK.

Scope today: indicators from bars. ``context`` (VIX/regime), ``news``, and
``events`` (economic calendar) are the next data tools; they return
empty/default for now, which the strategy and advisory treat as "no extra
signal" rather than guessing.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from trading_bot.domain.market_state import (
    EconomicEvent,
    Indicators,
    MarketContext,
    NewsItem,
)
from trading_bot.indicators.compute import IndicatorParams, compute_indicators

_OHLCV = ('open', 'high', 'low', 'close', 'volume')


class AlpacaMarketDataProvider:
    """Provides indicators computed from Alpaca minute bars."""

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        *,
        symbols: tuple[str, ...],
        lookback_days: int = 5,
        feed: str = 'iex',
        params: IndicatorParams | None = None,
        client: Any | None = None,
    ) -> None:
        self._symbols = symbols
        self._lookback = timedelta(days=lookback_days)
        self._feed = feed
        self._params = params or IndicatorParams()
        if client is not None:
            self._client = client
        else:
            from alpaca.data.historical import StockHistoricalDataClient

            self._client = StockHistoricalDataClient(api_key, secret_key)

    def indicators(self, as_of: datetime) -> dict[str, Indicators]:
        import pandas as pd
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        request = StockBarsRequest(
            symbol_or_symbols=list(self._symbols),
            timeframe=TimeFrame.Minute,
            start=as_of - self._lookback,
            end=as_of,
            feed=DataFeed(self._feed),
        )
        frame = self._client.get_stock_bars(request).df
        out: dict[str, Indicators] = {}
        if frame.empty:
            return out
        for symbol in self._symbols:
            if symbol not in frame.index.get_level_values(0):
                continue
            bars = frame.loc[symbol][list(_OHLCV)]
            if not isinstance(bars.index, pd.DatetimeIndex) or bars.index.tz is None:
                bars = bars.tz_localize('UTC') if bars.index.tz is None else bars
            out[symbol] = compute_indicators(bars, symbol, as_of, self._params)
        return out

    def context(self, as_of: datetime) -> MarketContext:
        return MarketContext()  # TODO: VIX / regime / premarket futures data tool

    def news(self, as_of: datetime) -> tuple[NewsItem, ...]:
        return ()  # TODO: Alpaca/Polygon/Finnhub timestamped news data tool

    def events(self, as_of: datetime) -> tuple[EconomicEvent, ...]:
        return ()  # TODO: economic-calendar (FOMC/CPI/jobs) data tool
