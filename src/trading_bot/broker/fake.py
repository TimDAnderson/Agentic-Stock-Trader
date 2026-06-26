"""In-memory broker for tests and offline local development.

Implements the full ``Broker`` surface with optimistic, deterministic fills so
the order flow (bracket entry, idempotency, EOD liquidation, MOC backstop) can
be exercised with **zero network**. It is intentionally simple: bracket entries
fill immediately at the symbol's current price; the take-profit/stop legs are
recorded but not auto-triggered (the backtester models touches; live relies on
the real broker's OCO).
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count

from trading_bot.broker.base import (
    Account,
    BracketOrder,
    Broker,
    BrokerMode,
    BrokerPosition,
    OrderResult,
    OrderSide,
    OrderStatus,
)
from trading_bot.broker.errors import DuplicateClientOrderIdError


class FakeBroker(Broker):
    """Deterministic in-memory broker.

    ``prices`` seeds the last-trade price per symbol used for fills; update it
    with :meth:`set_price` to simulate movement.
    """

    def __init__(
        self,
        mode: BrokerMode = BrokerMode.PAPER,
        *,
        equity: float = 100_000.0,
        prices: dict[str, float] | None = None,
    ) -> None:
        self.mode = mode
        self._equity = equity
        self._cash = equity
        self._prices: dict[str, float] = dict(prices or {})
        self._positions: dict[str, BrokerPosition] = {}
        self._orders: dict[str, OrderResult] = {}  # keyed by client_order_id
        self._resting_moc: dict[str, tuple[str, int]] = {}  # coid -> (symbol, qty)
        self._ids = count(1)

    # -- test/local controls ------------------------------------------------
    def set_price(self, symbol: str, price: float) -> None:
        self._prices[symbol] = price

    def _price(self, symbol: str) -> float:
        if symbol not in self._prices:
            raise KeyError(f'FakeBroker has no price for {symbol!r}; call set_price first.')
        return self._prices[symbol]

    def _new_id(self) -> str:
        return f'fake-{next(self._ids)}'

    def _store(self, result: OrderResult) -> OrderResult:
        """Record an order under its client_order_id for idempotency lookups."""
        self._orders[result.client_order_id] = result
        return result

    # -- Broker protocol ----------------------------------------------------
    def get_account(self) -> Account:
        return Account(
            account_id='FAKE',
            mode=self.mode,
            equity=self._equity,
            cash=self._cash,
            buying_power=self._cash,
        )

    def get_positions(self) -> list[BrokerPosition]:
        return [self._mark(p) for p in self._positions.values()]

    def get_position(self, symbol: str) -> BrokerPosition | None:
        pos = self._positions.get(symbol)
        return self._mark(pos) if pos is not None else None

    def get_order_by_client_id(self, client_order_id: str) -> OrderResult | None:
        return self._orders.get(client_order_id)

    def submit_bracket_buy(self, order: BracketOrder) -> OrderResult:
        if order.client_order_id in self._orders:
            raise DuplicateClientOrderIdError(f'Order {order.client_order_id!r} already submitted.')
        fill_price = self._price(order.symbol)
        self._positions[order.symbol] = BrokerPosition(
            symbol=order.symbol,
            qty=order.qty,
            avg_entry_price=fill_price,
            current_price=fill_price,
        )
        self._cash -= fill_price * order.qty
        return self._store(
            OrderResult(
                id=self._new_id(),
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                side=OrderSide.BUY,
                qty=order.qty,
                status=OrderStatus.FILLED,
                filled_qty=order.qty,
                filled_avg_price=fill_price,
                submitted_at=datetime.now(UTC),
            )
        )

    def submit_moc_sell(self, symbol: str, qty: int, client_order_id: str) -> OrderResult:
        if client_order_id in self._orders:
            raise DuplicateClientOrderIdError(f'Order {client_order_id!r} already submitted.')
        # A MOC *rests* until the closing auction — the position is NOT flattened
        # now. Call settle_market_on_close() to simulate the close filling it.
        self._resting_moc[client_order_id] = (symbol, qty)
        return self._store(
            OrderResult(
                id=self._new_id(),
                client_order_id=client_order_id,
                symbol=symbol,
                side=OrderSide.SELL,
                qty=qty,
                status=OrderStatus.NEW,
                filled_qty=0,
                filled_avg_price=None,
                submitted_at=datetime.now(UTC),
            )
        )

    def settle_market_on_close(self) -> list[OrderResult]:
        """Test control: simulate the closing auction filling resting MOC sells.

        Proves the EOD backstop survives a dead Lambda — the position flattens
        with no further engine run.
        """
        filled: list[OrderResult] = []
        for coid, (symbol, qty) in list(self._resting_moc.items()):
            self._close_symbol(symbol, qty)
            order = self._orders[coid].model_copy(
                update={
                    'status': OrderStatus.FILLED,
                    'filled_qty': qty,
                    'filled_avg_price': self._prices.get(symbol),
                }
            )
            self._orders[coid] = order
            filled.append(order)
        self._resting_moc.clear()
        return filled

    def close_all_positions(self, *, cancel_orders: bool = True) -> list[OrderResult]:
        if cancel_orders:
            self._resting_moc.clear()  # an immediate liquidation supersedes the MOC
        results: list[OrderResult] = []
        for symbol, pos in list(self._positions.items()):
            price = self._prices.get(symbol, pos.avg_entry_price)
            self._cash += price * pos.qty
            results.append(
                OrderResult(
                    id=self._new_id(),
                    client_order_id=f'liquidate-{symbol}',
                    symbol=symbol,
                    side=OrderSide.SELL,
                    qty=pos.qty,
                    status=OrderStatus.FILLED,
                    filled_qty=pos.qty,
                    filled_avg_price=price,
                    submitted_at=datetime.now(UTC),
                )
            )
        self._positions.clear()
        return results

    def cancel_all_orders(self) -> int:
        open_orders = [o for o in self._orders.values() if o.status.is_open]
        return len(open_orders)

    # -- helpers ------------------------------------------------------------
    def _close_symbol(self, symbol: str, qty: int) -> None:
        pos = self._positions.get(symbol)
        if pos is None:
            return
        remaining = pos.qty - qty
        if remaining <= 0:
            del self._positions[symbol]
        else:
            self._positions[symbol] = pos.model_copy(update={'qty': remaining})

    def _mark(self, pos: BrokerPosition) -> BrokerPosition:
        """Refresh a position's mark-to-market against the current price."""
        price = self._prices.get(pos.symbol, pos.current_price or pos.avg_entry_price)
        return pos.model_copy(
            update={
                'current_price': price,
                'market_value': price * pos.qty,
                'unrealized_pl': (price - pos.avg_entry_price) * pos.qty,
            }
        )
