"""Veto-only merge of a deterministic decision with the advisory (DECISIONS.md §7).

This is the single chokepoint that enforces the core principle: the advisory may
**downgrade a buy to DO_NOTHING, never the reverse**. A PROCEED leaves the
deterministic decision untouched; a VETO on a non-buy is a no-op. The LLM can
only make the system more cautious.
"""

from __future__ import annotations

from trading_bot.domain.decisions import EntryDecision
from trading_bot.reasoning.advisor import Advisory, Recommendation


def apply_advisory(decision: EntryDecision, advisory: Advisory) -> EntryDecision:
    if advisory.recommendation is Recommendation.PROCEED:
        return decision
    if decision.action.is_buy:
        return EntryDecision.do_nothing(
            f'Advisory VETO: {advisory.reason} '
            f'(deterministic was {decision.action.value}: {decision.reason})'
        )
    return decision  # nothing to veto
