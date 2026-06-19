"""Tests for typed decision invariants."""

from __future__ import annotations

import pytest

from trading_bot.domain.decisions import EntryAction, EntryDecision, ExitAction, ExitDecision


def test_buy_requires_qty_and_stop() -> None:
    with pytest.raises(ValueError):
        EntryDecision(action=EntryAction.BUY_BULLISH, reason='x')
    with pytest.raises(ValueError):
        EntryDecision(action=EntryAction.BUY_BULLISH, reason='x', qty=10)


def test_do_nothing_rejects_qty() -> None:
    with pytest.raises(ValueError):
        EntryDecision(action=EntryAction.DO_NOTHING, reason='x', qty=10)


def test_do_nothing_helper() -> None:
    d = EntryDecision.do_nothing('no conviction')
    assert d.action is EntryAction.DO_NOTHING
    assert d.qty is None


def test_action_is_buy() -> None:
    assert EntryAction.BUY_BULLISH.is_buy
    assert EntryAction.BUY_BEARISH.is_buy
    assert not EntryAction.DO_NOTHING.is_buy


def test_exit_helpers() -> None:
    assert ExitDecision.sell('close').action is ExitAction.SELL
    assert ExitDecision.hold('wait').action is ExitAction.HOLD
