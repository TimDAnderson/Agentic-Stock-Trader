"""Reasoning layer (DECISIONS.md §7).

The deterministic strategy decides; this layer can only **veto** a buy. It also
assembles the ``MarketState`` via parallel data gathering.

Only the LangGraph-free pieces are exported here, so importing the advisor/veto
(e.g. from the engine) does not require LangGraph. The graph-backed pieces live
in submodules and require the ``reasoning`` extra:

- ``trading_bot.reasoning.tot.ToTAdvisor``        — Tree-of-Thought advisory subgraph
- ``trading_bot.reasoning.graph.gather_market_state`` — parallel data-gathering graph
- ``trading_bot.reasoning.openrouter.OpenRouterLLM``  — real LLM client
"""

from trading_bot.reasoning.advisor import (
    Advisor,
    Advisory,
    FakeAdvisor,
    Recommendation,
    ThoughtBranch,
)
from trading_bot.reasoning.assembly import assemble_market_state
from trading_bot.reasoning.llm import FakeLLM, LLMClient
from trading_bot.reasoning.providers import MarketDataProvider, StaticMarketDataProvider
from trading_bot.reasoning.veto import apply_advisory

__all__ = [
    'Advisor',
    'Advisory',
    'FakeAdvisor',
    'Recommendation',
    'ThoughtBranch',
    'assemble_market_state',
    'FakeLLM',
    'LLMClient',
    'MarketDataProvider',
    'StaticMarketDataProvider',
    'apply_advisory',
]
