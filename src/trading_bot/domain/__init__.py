"""Typed, pure domain data shared across the bot.

Nothing here imports broker / AWS / LLM code. These types are the stable
contract that the strategy and backtester are written against.
"""

from trading_bot.domain.config import StrategyConfig
from trading_bot.domain.decisions import (
    EntryAction,
    EntryDecision,
    ExitAction,
    ExitDecision,
)
from trading_bot.domain.market_state import (
    EconomicEvent,
    Indicators,
    MarketContext,
    MarketState,
    NewsItem,
    Position,
)

__all__ = [
    'StrategyConfig',
    'EntryAction',
    'EntryDecision',
    'ExitAction',
    'ExitDecision',
    'EconomicEvent',
    'Indicators',
    'MarketContext',
    'MarketState',
    'NewsItem',
    'Position',
]
