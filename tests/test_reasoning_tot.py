"""Tests for the Tree-of-Thought advisory subgraph (uses FakeLLM — no network)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from trading_bot.domain.decisions import EntryAction, EntryDecision
from trading_bot.domain.market_state import Indicators, MarketState
from trading_bot.reasoning.advisor import Recommendation
from trading_bot.reasoning.llm import FakeLLM
from trading_bot.reasoning.tot import ToTAdvisor

ET = ZoneInfo('America/New_York')


def _state() -> MarketState:
    return MarketState(
        as_of=datetime(2026, 6, 2, 10, 0, tzinfo=ET),
        indicators={'QQQ': Indicators(symbol='QQQ', price=400.0, rsi=60.0, macd_hist=0.2)},
        equity=100_000.0,
    )


def _buy() -> EntryDecision:
    return EntryDecision(
        action=EntryAction.BUY_BULLISH, qty=10, stop_loss_price=395.0, reason='momentum'
    )


def test_evaluator_veto_produces_veto_with_four_calls() -> None:
    llm = FakeLLM(verdict='VETO')
    advisory = ToTAdvisor(llm).advise(_state(), _buy())
    assert advisory.recommendation is Recommendation.VETO
    assert len(advisory.branches) == 3  # bull / bear / neutral
    assert advisory.llm_calls == 4  # 3 branches + 1 evaluator
    assert len(llm.calls) == 4


def test_evaluator_proceed_produces_proceed() -> None:
    advisory = ToTAdvisor(FakeLLM(verdict='PROCEED')).advise(_state(), _buy())
    assert advisory.recommendation is Recommendation.PROCEED


def test_unclear_evaluator_defaults_to_veto() -> None:
    # Bias toward caution: anything not an explicit verdict becomes VETO.
    advisory = ToTAdvisor(FakeLLM(verdict='I am not sure about this one')).advise(_state(), _buy())
    assert advisory.recommendation is Recommendation.VETO
