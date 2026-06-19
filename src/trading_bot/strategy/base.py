"""The Strategy interface (DECISIONS.md §6).

A strategy is a **pure function of (MarketState, StrategyConfig)** — no AWS, no
LLM, no broker call. That is what makes it unit-testable and backtestable, and
what lets the LangGraph reasoning layer wrap it without changing it.

The deterministic strategy *decides*. The LLM/ToT advisory pass may only
veto/downgrade a buy to DO_NOTHING — it can never create a buy (DECISIONS.md §7).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from trading_bot.domain.config import StrategyConfig
from trading_bot.domain.decisions import EntryDecision, ExitDecision
from trading_bot.domain.market_state import MarketState, Position


@runtime_checkable
class Strategy(Protocol):
    version: str  # stamped on every trade record for attribution & rollback

    def evaluate_entry(self, state: MarketState, config: StrategyConfig) -> EntryDecision: ...

    def evaluate_exit(
        self, state: MarketState, position: Position, config: StrategyConfig
    ) -> ExitDecision: ...
