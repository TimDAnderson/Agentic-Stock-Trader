"""Tree-of-Thought advisory subgraph (DECISIONS.md §7).

Parallel bull / bear / neutral branches → an evaluator → a PROCEED/VETO
recommendation. Built as a small LangGraph so the three branches fan out
concurrently (bounded by the slowest LLM call, not the sum) and fan into the
evaluator. The evaluator is **biased toward VETO**: anything ambiguous, empty,
or unparseable defaults to VETO. This structures reasoning; it does not add
predictive accuracy, and per the veto-only merge it can only make the system
more cautious.

The LLM is injected (``LLMClient``), so the whole subgraph runs deterministically
in tests with ``FakeLLM`` — no network.
"""

from __future__ import annotations

import operator
from collections.abc import Callable
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from trading_bot.domain.decisions import EntryDecision
from trading_bot.domain.market_state import MarketState
from trading_bot.reasoning.advisor import Advisory, Recommendation, ThoughtBranch
from trading_bot.reasoning.llm import LLMClient

_EVALUATOR_MARKER = 'You are the risk evaluator'


class _ToTState(TypedDict, total=False):
    context: str
    bull: str
    bear: str
    neutral: str
    recommendation: Recommendation
    reason: str
    llm_calls: Annotated[int, operator.add]


def _branch_prompt(stance: str, guidance: str) -> str:
    return (
        f'You are the {stance} analyst for an intraday trade. {guidance} '
        'Be concise (2-3 sentences). Reason only from the data given.'
    )


def _parse_verdict(text: str) -> tuple[Recommendation, str]:
    upper = text.upper()
    first_line = text.strip().splitlines()[0] if text.strip() else ''
    # Bias toward VETO: only an explicit, unambiguous PROCEED proceeds.
    if 'VETO' in upper:
        return Recommendation.VETO, first_line or 'Evaluator vetoed.'
    if 'PROCEED' in upper:
        return Recommendation.PROCEED, first_line or 'Evaluator approved.'
    return Recommendation.VETO, 'Evaluator response unclear; defaulting to VETO.'


class ToTAdvisor:
    """LLM-backed advisory pass; safe-defaults to VETO on any failure."""

    def __init__(self, llm: LLMClient, *, per_call_timeout: float = 30.0) -> None:
        self._llm = llm
        self._timeout = per_call_timeout
        self._graph = self._build()

    def _build(self) -> Any:
        graph = StateGraph(_ToTState)
        graph.add_node('bull', self._make_branch('BULL', 'Argue why the trade could work.'))
        graph.add_node('bear', self._make_branch('BEAR', 'Argue why the trade could fail.'))
        graph.add_node('neutral', self._make_branch('NEUTRAL', 'Weigh both sides dispassionately.'))
        graph.add_node('evaluator', self._evaluate)
        for branch in ('bull', 'bear', 'neutral'):
            graph.add_edge(START, branch)
            graph.add_edge(branch, 'evaluator')
        graph.add_edge('evaluator', END)
        return graph.compile()

    def _make_branch(self, stance: str, guidance: str) -> Callable[[_ToTState], dict[str, Any]]:
        key = stance.lower()
        system = _branch_prompt(stance, guidance)

        def node(state: _ToTState) -> dict[str, Any]:
            text = self._llm.complete(system, state['context'], timeout=self._timeout)
            return {key: text, 'llm_calls': 1}

        return node

    def _evaluate(self, state: _ToTState) -> dict[str, Any]:
        system = (
            f'{_EVALUATOR_MARKER}. Given the bull, bear, and neutral analyses of a proposed '
            'intraday trade, decide PROCEED or VETO. Bias strongly toward VETO when uncertain — '
            'a missed trade is cheap, a bad trade is expensive. Put PROCEED or VETO on the first '
            'line, then a one-sentence reason.'
        )
        user = (
            f'BULL:\n{state.get("bull", "")}\n\n'
            f'BEAR:\n{state.get("bear", "")}\n\n'
            f'NEUTRAL:\n{state.get("neutral", "")}'
        )
        text = self._llm.complete(system, user, timeout=self._timeout)
        recommendation, reason = _parse_verdict(text)
        return {'recommendation': recommendation, 'reason': reason, 'llm_calls': 1}

    def advise(self, state: MarketState, decision: EntryDecision) -> Advisory:
        context = _summarize(state, decision)
        try:
            result: dict[str, Any] = self._graph.invoke(
                {'context': context, 'llm_calls': 0}, {'recursion_limit': 8}
            )
        except Exception as exc:  # noqa: BLE001 - any failure must default to caution
            return Advisory(
                recommendation=Recommendation.VETO,
                reason=f'Advisory failed ({type(exc).__name__}); defaulting to VETO.',
            )
        branches = tuple(
            ThoughtBranch(stance=stance, thesis=result.get(stance, ''))
            for stance in ('bull', 'bear', 'neutral')
        )
        return Advisory(
            recommendation=result['recommendation'],
            reason=result['reason'],
            branches=branches,
            llm_calls=result.get('llm_calls', 0),
        )


def _summarize(state: MarketState, decision: EntryDecision) -> str:
    lines = [
        f'Proposed action: {decision.action.value} '
        f'(qty={decision.qty}, stop={decision.stop_loss_price}).',
        f'Deterministic reason: {decision.reason}',
        f'As of {state.as_of.isoformat()}, equity ${state.equity:,.0f}.',
    ]
    for symbol, ind in state.indicators.items():
        lines.append(
            f'{symbol}: price={ind.price} rsi={ind.rsi} macd_hist={ind.macd_hist} '
            f'vwap={ind.vwap} atr={ind.atr} rel_vol={ind.relative_volume}'
        )
    if state.context.vix is not None:
        lines.append(f'VIX={state.context.vix} regime={state.context.regime}')
    if state.events:
        lines.append(
            'Upcoming events: '
            + ', '.join(f'{e.name}@{e.timestamp.isoformat()}({e.impact})' for e in state.events)
        )
    for item in state.news[:5]:
        lines.append(f'News {item.timestamp.isoformat()}: {item.headline}')
    return '\n'.join(lines)
