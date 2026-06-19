"""Broker interface + typed value objects (DECISIONS.md §2, §5).

The rest of the system depends on the ``Broker`` Protocol, never on a concrete
SDK. That dependency inversion is what lets us run the exact same order flow
against the in-memory ``FakeBroker`` (tests/local) and the real ``AlpacaBroker``
(paper/live) by swapping one object.

All value objects are frozen pydantic models so they validate on construction
and serialize cleanly into the audit record.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict


class BrokerMode(StrEnum):
    """Explicit, required trading mode — no default (DECISIONS.md §2).

    Code must refuse to start if mode is unset/invalid; it must never fall
    through to live.
    """

    PAPER = 'paper'
    LIVE = 'live'


class OrderSide(StrEnum):
    BUY = 'buy'
    SELL = 'sell'


class OrderStatus(StrEnum):
    """Normalized order status, decoupled from any SDK's enum spelling."""

    NEW = 'new'
    PENDING = 'pending'
    PARTIALLY_FILLED = 'partially_filled'
    FILLED = 'filled'
    CANCELED = 'canceled'
    REJECTED = 'rejected'
    EXPIRED = 'expired'
    UNKNOWN = 'unknown'

    @property
    def is_open(self) -> bool:
        return self in (OrderStatus.NEW, OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED)

    @property
    def is_done(self) -> bool:
        return self in (
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        )


class Account(BaseModel):
    """Snapshot of the connected account."""

    model_config = ConfigDict(frozen=True)

    account_id: str
    mode: BrokerMode
    equity: float
    cash: float
    buying_power: float


class BrokerPosition(BaseModel):
    """An open position as the broker reports it (source of truth, §4)."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    qty: int
    avg_entry_price: float
    current_price: float | None = None
    market_value: float | None = None
    unrealized_pl: float | None = None
    side: str = 'long'


class BracketOrder(BaseModel):
    """A market entry plus an OCO take-profit / stop-loss pair (DECISIONS.md §5).

    Every entry is a bracket so the position gets an automatic stop on fill.
    ``client_order_id`` is the date-keyed idempotency key (see ``broker.ids``).
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    qty: int
    stop_loss_price: float
    take_profit_price: float | None = None
    client_order_id: str
    time_in_force: str = 'day'  # DAY only cancels *unfilled* orders, never a fill


class OrderResult(BaseModel):
    """Normalized result of an order submission/lookup."""

    model_config = ConfigDict(frozen=True)

    id: str  # broker-assigned order id
    client_order_id: str
    symbol: str
    side: OrderSide
    qty: int
    status: OrderStatus
    filled_qty: int = 0
    filled_avg_price: float | None = None
    submitted_at: datetime | None = None
    # Trimmed raw payload for the audit log; large blobs go to S3 (DECISIONS.md §9).
    raw: dict[str, Any] | None = None


@runtime_checkable
class Broker(Protocol):
    """The capability surface the bot needs from a broker.

    Implementations: ``FakeBroker`` (in-memory) and ``AlpacaBroker`` (real).
    """

    mode: BrokerMode

    def get_account(self) -> Account: ...

    def get_positions(self) -> list[BrokerPosition]: ...

    def get_position(self, symbol: str) -> BrokerPosition | None: ...

    def get_order_by_client_id(self, client_order_id: str) -> OrderResult | None: ...

    def submit_bracket_buy(self, order: BracketOrder) -> OrderResult: ...

    def submit_moc_sell(self, symbol: str, qty: int, client_order_id: str) -> OrderResult: ...

    def close_all_positions(self, *, cancel_orders: bool = True) -> list[OrderResult]: ...

    def cancel_all_orders(self) -> int: ...
