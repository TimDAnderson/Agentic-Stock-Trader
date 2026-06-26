"""Advisory layer types (DECISIONS.md §7).

The advisory pass structures reasoning and can **veto/downgrade a buy to
do-nothing — it can never create a buy**. The deterministic strategy holds the
wheel; this only ever makes the system *more* cautious.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from trading_bot.domain.decisions import EntryDecision
from trading_bot.domain.market_state import MarketState


class Recommendation(StrEnum):
    PROCEED = 'PROCEED'
    VETO = 'VETO'


class ThoughtBranch(BaseModel):
    """One branch of the Tree-of-Thought (bull / bear / neutral)."""

    model_config = ConfigDict(frozen=True)

    stance: str
    thesis: str


class Advisory(BaseModel):
    """Result of the advisory pass — captured in the audit record."""

    model_config = ConfigDict(frozen=True)

    recommendation: Recommendation
    reason: str
    branches: tuple[ThoughtBranch, ...] = ()
    llm_calls: int = 0


@runtime_checkable
class Advisor(Protocol):
    def advise(self, state: MarketState, decision: EntryDecision) -> Advisory: ...


class FakeAdvisor:
    """Deterministic advisor for tests/local — always returns the configured call."""

    def __init__(
        self, recommendation: Recommendation = Recommendation.PROCEED, reason: str = 'fake advisor'
    ) -> None:
        self._recommendation = recommendation
        self._reason = reason

    def advise(self, state: MarketState, decision: EntryDecision) -> Advisory:
        return Advisory(recommendation=self._recommendation, reason=self._reason)
