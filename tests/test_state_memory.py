"""Tests for InMemoryStateRepository — focus on the conditional-write gate."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from trading_bot.broker.base import BrokerMode, OrderSide, OrderStatus
from trading_bot.state import (
    ConcurrentTransitionError,
    DailyState,
    InMemoryStateRepository,
    PositionStatus,
    RunRecord,
    StateAlreadyExistsError,
    TradeRecord,
)

TD = date(2026, 6, 2)
NOW = datetime(2026, 6, 2, 14, 0, tzinfo=UTC)


def _state(status: PositionStatus = PositionStatus.NO_POSITION) -> DailyState:
    return DailyState(
        trade_date=TD,
        status=status,
        strategy_version='v1',
        mode=BrokerMode.PAPER,
        created_at=NOW,
        updated_at=NOW,
    )


def test_create_then_get() -> None:
    repo = InMemoryStateRepository()
    assert repo.get_daily_state(TD) is None
    repo.create_daily_state(_state())
    got = repo.get_daily_state(TD)
    assert got is not None and got.status is PositionStatus.NO_POSITION


def test_create_is_idempotent_guard() -> None:
    repo = InMemoryStateRepository()
    repo.create_daily_state(_state())
    with pytest.raises(StateAlreadyExistsError):
        repo.create_daily_state(_state())


def test_transition_succeeds_when_expected_matches() -> None:
    repo = InMemoryStateRepository()
    repo.create_daily_state(_state())
    moved = _state().transitioned(PositionStatus.POSITION_OPEN, NOW, symbol='QQQ', qty=10)
    repo.transition_status(PositionStatus.NO_POSITION, moved)
    got = repo.get_daily_state(TD)
    assert got is not None and got.status is PositionStatus.POSITION_OPEN
    assert got.revision == 1


def test_transition_rejected_when_expected_mismatches() -> None:
    repo = InMemoryStateRepository()
    repo.create_daily_state(_state(PositionStatus.POSITION_OPEN))
    # Another run thinks it's still NO_POSITION -> guard fails (no double-buy).
    moved = _state().transitioned(PositionStatus.POSITION_OPEN, NOW)
    with pytest.raises(ConcurrentTransitionError):
        repo.transition_status(PositionStatus.NO_POSITION, moved)


def test_append_and_list_runs_and_trades() -> None:
    repo = InMemoryStateRepository()
    repo.append_run(
        RunRecord(
            trade_date=TD,
            ts=NOW,
            action='DO_NOTHING',
            reason='quiet',
            status_before=PositionStatus.NO_POSITION,
            status_after=PositionStatus.NO_POSITION,
            strategy_version='v1',
            mode=BrokerMode.PAPER,
        )
    )
    repo.append_trade(
        TradeRecord(
            trade_date=TD,
            order_id='2026-06-02-ENTRY',
            broker_order_id='abc',
            kind='entry',
            symbol='QQQ',
            side=OrderSide.BUY,
            qty=10,
            status=OrderStatus.FILLED,
            strategy_version='v1',
            mode=BrokerMode.PAPER,
        )
    )
    assert len(repo.list_runs(TD)) == 1
    assert len(repo.list_trades(TD)) == 1
    assert len(repo.list_trades_by_version('v1')) == 1
    assert repo.list_trades_by_version('v2') == []
