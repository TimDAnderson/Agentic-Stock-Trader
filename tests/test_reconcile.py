"""Tests for the broker-vs-DB reconciliation rules (DECISIONS.md §4)."""

from __future__ import annotations

from trading_bot.broker.base import BrokerPosition
from trading_bot.state.models import PositionStatus
from trading_bot.state.reconcile import ReconcileAction, reconcile


def _pos() -> BrokerPosition:
    return BrokerPosition(symbol='QQQ', qty=10, avg_entry_price=400.0)


def test_no_position_and_flat_proceeds() -> None:
    r = reconcile(PositionStatus.NO_POSITION, None)
    assert r.action is ReconcileAction.PROCEED
    assert r.status is PositionStatus.NO_POSITION


def test_no_position_but_broker_holds_halts() -> None:
    r = reconcile(PositionStatus.NO_POSITION, _pos())
    assert r.action is ReconcileAction.HALT
    assert r.alert is not None


def test_open_and_holding_proceeds() -> None:
    r = reconcile(PositionStatus.POSITION_OPEN, _pos())
    assert r.action is ReconcileAction.PROCEED
    assert r.status is PositionStatus.POSITION_OPEN


def test_open_but_broker_flat_marks_closed() -> None:
    # The bracket stop or EOD exit already fired — close out, don't re-sell.
    r = reconcile(PositionStatus.POSITION_OPEN, None)
    assert r.action is ReconcileAction.MARK_CLOSED
    assert r.status is PositionStatus.POSITION_CLOSED


def test_closed_and_flat_proceeds() -> None:
    r = reconcile(PositionStatus.POSITION_CLOSED, None)
    assert r.action is ReconcileAction.PROCEED


def test_closed_but_broker_holds_halts() -> None:
    r = reconcile(PositionStatus.POSITION_CLOSED, _pos())
    assert r.action is ReconcileAction.HALT
    assert r.alert is not None
