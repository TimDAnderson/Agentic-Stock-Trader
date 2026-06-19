"""Integration tests for DynamoStateRepository against DynamoDB Local.

These prove the *real* conditional-write semantics (create-if-absent, guarded
transitions) and the GSI query — the parts the in-memory repo can only emulate.

Skipped unless DynamoDB Local is reachable and boto3 is installed:

    uv sync --extra aws
    docker compose up -d dynamodb
    DYNAMODB_ENDPOINT=http://localhost:8000 uv run --extra aws pytest tests/test_state_dynamodb.py

(or simply: `make test-int`)
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest

from trading_bot.broker.base import BrokerMode, OrderSide, OrderStatus
from trading_bot.state import (
    ConcurrentTransitionError,
    DailyState,
    PositionStatus,
    StateAlreadyExistsError,
    TradeRecord,
)
from trading_bot.state.dynamodb import DynamoStateRepository

_ENDPOINT = os.environ.get('DYNAMODB_ENDPOINT')
_HAS_BOTO3 = importlib.util.find_spec('boto3') is not None

pytestmark = pytest.mark.skipif(
    not (_ENDPOINT and _HAS_BOTO3),
    reason='requires DynamoDB Local (set DYNAMODB_ENDPOINT) and the aws extra (boto3)',
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


@pytest.fixture
def repo() -> Iterator[DynamoStateRepository]:
    # DynamoDB Local ignores credential values but boto3 still requires them.
    os.environ.setdefault('AWS_ACCESS_KEY_ID', 'local')
    os.environ.setdefault('AWS_SECRET_ACCESS_KEY', 'local')
    table_name = f'trading-bot-test-{uuid.uuid4().hex[:8]}'
    r = DynamoStateRepository(table_name, region_name='us-east-1', endpoint_url=_ENDPOINT)
    r.ensure_table()
    yield r


def test_create_then_get_roundtrips(repo: DynamoStateRepository) -> None:
    repo.create_daily_state(_state())
    got = repo.get_daily_state(TD)
    assert got is not None
    assert got.status is PositionStatus.NO_POSITION
    assert got.strategy_version == 'v1'


def test_conditional_create_rejects_duplicate(repo: DynamoStateRepository) -> None:
    repo.create_daily_state(_state())
    with pytest.raises(StateAlreadyExistsError):
        repo.create_daily_state(_state())


def test_guarded_transition_enforced_server_side(repo: DynamoStateRepository) -> None:
    repo.create_daily_state(_state())
    moved = _state().transitioned(PositionStatus.POSITION_OPEN, NOW, symbol='QQQ', qty=10)
    repo.transition_status(PositionStatus.NO_POSITION, moved)
    assert repo.get_daily_state(TD).status is PositionStatus.POSITION_OPEN  # type: ignore[union-attr]

    # The slot is taken; a second run guarding on NO_POSITION must fail (no double-buy).
    with pytest.raises(ConcurrentTransitionError):
        repo.transition_status(PositionStatus.NO_POSITION, moved)


def test_trade_records_queryable_by_version(repo: DynamoStateRepository) -> None:
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
            filled_avg_price=400.0,
            strategy_version='v1',
            mode=BrokerMode.PAPER,
        )
    )
    by_day = repo.list_trades(TD)
    assert len(by_day) == 1 and by_day[0].filled_avg_price == 400.0
    by_version = repo.list_trades_by_version('v1')
    assert len(by_version) == 1
    assert repo.list_trades_by_version('v2') == []
