"""Intraday agentic QQQ/PSQ trading bot.

Package layout mirrors the separation-of-concerns contract in DECISIONS.md §6:

- ``trading_bot.domain``     — typed, pure data: MarketState, Decisions, StrategyConfig, Position.
- ``trading_bot.indicators`` — code-computed signals from OHLCV (with look-ahead protection).
- ``trading_bot.strategy``   — the deterministic, versioned, backtestable decision rule.
- ``trading_bot.backtest``   — replay historical MarketState snapshots through a strategy.

Plumbing (broker, persistence, reasoning, IaC) is layered on top and never leaks
into the strategy, which stays a pure function of ``(MarketState, StrategyConfig)``.
"""

__all__ = ['__version__']

__version__ = '0.1.0'
