"""Persistence models — the durable decision/trade system-of-record (DECISIONS.md §9).

Single-table design (one DynamoDB table):
- ``DailyState`` — mutable per-day row (``SK="STATE"``); the conditional-write gate.
- ``RunRecord``  — append-only, one per run (``SK="RUN#<ts>"``); captures every
  decision, including the many "do nothing" runs.
- ``TradeRecord`` — append-only, one per actual order (``SK="TRADE#<order_id>"``).

Every record is stamped with ``strategy_version`` and ``mode`` for attribution,
rollback, and clean paper/live separation.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from trading_bot.broker.base import BrokerMode, OrderSide, OrderStatus


class PositionStatus(StrEnum):
    """The buy-once / sell-once daily state machine (DECISIONS.md §4).

    ``NO_POSITION -> POSITION_OPEN -> POSITION_CLOSED`` (closed is terminal for
    the day; state is keyed by trade_date so it resets naturally each morning).
    """

    NO_POSITION = 'NO_POSITION'
    POSITION_OPEN = 'POSITION_OPEN'
    POSITION_CLOSED = 'POSITION_CLOSED'


class DailyState(BaseModel):
    """Mutable per-day state row — the gate that prevents double entries."""

    model_config = ConfigDict(frozen=True)

    trade_date: date
    status: PositionStatus = PositionStatus.NO_POSITION
    strategy_version: str
    mode: BrokerMode
    symbol: str | None = None
    qty: int | None = None
    entry_order_id: str | None = None
    exit_order_id: str | None = None
    moc_order_id: str | None = None  # the EOD market-on-close backstop, once placed
    created_at: datetime
    updated_at: datetime
    revision: int = 0  # bumped on every transition (optimistic-lock breadcrumb)

    def transitioned(self, status: PositionStatus, now: datetime, **changes: Any) -> DailyState:
        """Return a copy moved to ``status`` with ``updated_at``/``revision`` bumped."""
        return self.model_copy(
            update={
                'status': status,
                'updated_at': now,
                'revision': self.revision + 1,
                **changes,
            }
        )


class RunRecord(BaseModel):
    """Append-only record of a single run's decision (DECISIONS.md §9)."""

    model_config = ConfigDict(frozen=True)

    trade_date: date
    ts: datetime
    action: str  # EntryAction/ExitAction value, or a reconcile outcome
    reason: str
    status_before: PositionStatus
    status_after: PositionStatus
    strategy_version: str
    mode: BrokerMode
    duration_ms: int | None = None
    llm_calls: int | None = None
    market_snapshot: dict[str, Any] | None = None
    advisory: dict[str, Any] | None = None
    alert: str | None = None


class TradeRecord(BaseModel):
    """Append-only record of one actual order (DECISIONS.md §9)."""

    model_config = ConfigDict(frozen=True)

    trade_date: date
    order_id: str  # client_order_id (idempotency key)
    broker_order_id: str
    kind: str  # 'entry' | 'exit' | 'moc'
    symbol: str
    side: OrderSide
    qty: int
    status: OrderStatus
    filled_qty: int = 0
    filled_avg_price: float | None = None
    stop_loss_price: float | None = None
    take_profit_price: float | None = None
    pnl: float | None = None
    strategy_version: str
    mode: BrokerMode
    submitted_at: datetime | None = None
