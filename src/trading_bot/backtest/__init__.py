"""Local backtest harness with strict look-ahead protection (DECISIONS.md §10, §11).

Backtesting == replaying historical ``MarketState`` snapshots through a
``Strategy``. The harness reuses the exact same ``compute_indicators`` and
strategy code that runs in production, so what you test is what you ship.

Caveat (DECISIONS.md §11, §14): this proves the system *works* and lets you
compare strategy versions. It does **not** prove edge — fills here are
optimistic and backtests can flatter. "Does it work?" and "does it make money?"
stay separate questions.
"""

from trading_bot.backtest.harness import (
    Backtester,
    BacktestResult,
    Trade,
)

__all__ = ['BacktestResult', 'Backtester', 'Trade']
