"""Tests for the veto-only merge — the core 'LLM can only veto' guarantee (§7)."""

from __future__ import annotations

from trading_bot.domain.decisions import EntryAction, EntryDecision
from trading_bot.reasoning.advisor import Advisory, Recommendation
from trading_bot.reasoning.veto import apply_advisory


def _buy() -> EntryDecision:
    return EntryDecision(
        action=EntryAction.BUY_BULLISH, qty=10, stop_loss_price=395.0, reason='deterministic buy'
    )


def test_proceed_leaves_buy_untouched() -> None:
    advisory = Advisory(recommendation=Recommendation.PROCEED, reason='looks fine')
    assert apply_advisory(_buy(), advisory) == _buy()


def test_veto_downgrades_buy_to_do_nothing() -> None:
    advisory = Advisory(recommendation=Recommendation.VETO, reason='event risk')
    out = apply_advisory(_buy(), advisory)
    assert out.action is EntryAction.DO_NOTHING
    assert out.qty is None
    assert 'VETO' in out.reason and 'event risk' in out.reason


def test_veto_cannot_create_a_buy() -> None:
    # The advisory can never turn a DO_NOTHING into a buy — it only ever vetoes.
    do_nothing = EntryDecision.do_nothing('no conviction')
    for rec in (Recommendation.PROCEED, Recommendation.VETO):
        out = apply_advisory(do_nothing, Advisory(recommendation=rec, reason='x'))
        assert out.action is EntryAction.DO_NOTHING
