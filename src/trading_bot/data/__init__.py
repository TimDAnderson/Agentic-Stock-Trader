"""Data tools — concrete ``MarketDataProvider`` implementations (DECISIONS.md §8).

``AlpacaMarketDataProvider`` fetches bars and computes indicators with the same
``compute_indicators`` used by the backtester. News / economic-calendar / VIX
sourcing are the next data tools to add; until then this provider returns those
as empty so the deterministic, indicator-driven flow works end-to-end.
"""

from trading_bot.data.alpaca_data import AlpacaMarketDataProvider

__all__ = ['AlpacaMarketDataProvider']
