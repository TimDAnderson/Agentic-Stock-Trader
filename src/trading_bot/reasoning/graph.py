"""Parallel data-gathering graph (DECISIONS.md §7, §8).

Fans out to four concurrent gather nodes (indicators / context / news / events)
and fans in to ``assemble`` the ``MarketState``. Wall-clock is bounded by the
slowest fetch, not the sum. Built with LangGraph so this mirrors the eventual
full run graph; the providers are injected, so it runs network-free in tests.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from trading_bot.domain.market_state import (
    EconomicEvent,
    Indicators,
    MarketContext,
    MarketState,
    NewsItem,
)
from trading_bot.reasoning.assembly import assemble_market_state
from trading_bot.reasoning.providers import MarketDataProvider


class _GatherState(TypedDict, total=False):
    as_of: datetime
    equity: float
    indicators: dict[str, Indicators]
    context: MarketContext
    news: tuple[NewsItem, ...]
    events: tuple[EconomicEvent, ...]
    market_state: MarketState


def build_gather_graph(provider: MarketDataProvider) -> Any:
    """Compile a LangGraph that gathers data in parallel and assembles MarketState."""

    def gather_indicators(state: _GatherState) -> dict[str, Any]:
        return {'indicators': provider.indicators(state['as_of'])}

    def gather_context(state: _GatherState) -> dict[str, Any]:
        return {'context': provider.context(state['as_of'])}

    def gather_news(state: _GatherState) -> dict[str, Any]:
        return {'news': provider.news(state['as_of'])}

    def gather_events(state: _GatherState) -> dict[str, Any]:
        return {'events': provider.events(state['as_of'])}

    def assemble(state: _GatherState) -> dict[str, Any]:
        return {
            'market_state': assemble_market_state(
                state['as_of'],
                state['equity'],
                indicators=state['indicators'],
                context=state['context'],
                news=state['news'],
                events=state['events'],
            )
        }

    graph = StateGraph(_GatherState)
    nodes = {
        'indicators': gather_indicators,
        'context': gather_context,
        'news': gather_news,
        'events': gather_events,
    }
    for name, fn in nodes.items():
        graph.add_node(name, fn)
        graph.add_edge(START, name)
        graph.add_edge(name, 'assemble')
    graph.add_node('assemble', assemble)
    graph.add_edge('assemble', END)
    return graph.compile()


def gather_market_state(
    provider: MarketDataProvider,
    as_of: datetime,
    equity: float,
    *,
    recursion_limit: int = 10,
) -> MarketState:
    """Run the gather graph once and return the assembled MarketState."""
    graph = build_gather_graph(provider)
    result: dict[str, Any] = graph.invoke(
        {'as_of': as_of, 'equity': equity}, {'recursion_limit': recursion_limit}
    )
    market_state = result['market_state']
    assert isinstance(market_state, MarketState)
    return market_state
