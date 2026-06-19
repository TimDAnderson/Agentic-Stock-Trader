"""Alpaca adapter implementing the ``Broker`` Protocol (DECISIONS.md §2, §5).

Translates our typed value objects to/from alpaca-py. The SDK is an **optional**
dependency (``uv sync --extra broker``) and is imported lazily, so the rest of
the package — and all unit tests — work without it installed.

Design points enforced here:
- ``mode`` is required and explicit; the account is fetched at startup to verify
  the keys match the intended paper/live endpoint before any order is placed.
- Entries are OCO **bracket** orders (entry + take-profit + stop-loss).
- EOD exit uses ``close_all_positions``; the MOC order is the broker-side
  backstop that survives a failed scheduler run.
"""

from __future__ import annotations

from typing import Any, NamedTuple

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
from trading_bot.broker.credentials import BrokerCredentials
from trading_bot.broker.errors import (
    AccountModeMismatchError,
    BrokerError,
    BrokerNotConfiguredError,
)

_STATUS_MAP: dict[str, OrderStatus] = {
    'new': OrderStatus.NEW,
    'accepted': OrderStatus.NEW,
    'pending_new': OrderStatus.PENDING,
    'accepted_for_bidding': OrderStatus.PENDING,
    'partially_filled': OrderStatus.PARTIALLY_FILLED,
    'filled': OrderStatus.FILLED,
    'canceled': OrderStatus.CANCELED,
    'pending_cancel': OrderStatus.CANCELED,
    'rejected': OrderStatus.REJECTED,
    'expired': OrderStatus.EXPIRED,
}


class _Sdk(NamedTuple):
    """The alpaca-py symbols we use, loaded once."""

    TradingClient: Any
    MarketOrderRequest: Any
    TakeProfitRequest: Any
    StopLossRequest: Any
    OrderSide: Any
    OrderClass: Any
    TimeInForce: Any
    APIError: Any


def _load_sdk() -> _Sdk:
    try:
        from alpaca.common.exceptions import APIError
        from alpaca.trading.client import TradingClient
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import (
            MarketOrderRequest,
            StopLossRequest,
            TakeProfitRequest,
        )
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise BrokerNotConfiguredError(
            'alpaca-py is not installed. Install the broker extra: `uv sync --extra broker`.'
        ) from exc
    return _Sdk(
        TradingClient=TradingClient,
        MarketOrderRequest=MarketOrderRequest,
        TakeProfitRequest=TakeProfitRequest,
        StopLossRequest=StopLossRequest,
        OrderSide=OrderSide,
        OrderClass=OrderClass,
        TimeInForce=TimeInForce,
        APIError=APIError,
    )


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


def _i(value: Any) -> int:
    return int(float(value))


def _normalize_status(value: Any) -> OrderStatus:
    text = str(getattr(value, 'value', value)).rsplit('.', 1)[-1].lower()
    return _STATUS_MAP.get(text, OrderStatus.UNKNOWN)


class AlpacaBroker(Broker):
    """Live/paper broker backed by alpaca-py."""

    def __init__(
        self,
        mode: BrokerMode,
        credentials: BrokerCredentials,
        *,
        verify: bool = True,
    ) -> None:
        self.mode = mode
        self._sdk = _load_sdk()
        self._client = self._sdk.TradingClient(
            credentials.api_key,
            credentials.secret_key,
            paper=(mode is BrokerMode.PAPER),
        )
        self._account_id: str | None = None
        if verify:
            self._verify_account()

    def _verify_account(self) -> None:
        """Fetch the account so wrong keys/mode fail loudly before any order.

        Paper and live keys are not interchangeable, so a successful fetch on the
        mode-selected endpoint confirms the connection matches the intended mode.
        """
        try:
            account = self._client.get_account()
        except self._sdk.APIError as exc:
            raise AccountModeMismatchError(
                f'Could not authenticate the {self.mode.value} account '
                f'(keys may be for the wrong mode): {exc}'
            ) from exc
        self._account_id = str(account.id)

    # -- Broker protocol ----------------------------------------------------
    def get_account(self) -> Account:
        a = self._client.get_account()
        return Account(
            account_id=str(a.id),
            mode=self.mode,
            equity=_f(a.equity) or 0.0,
            cash=_f(a.cash) or 0.0,
            buying_power=_f(a.buying_power) or 0.0,
        )

    def get_positions(self) -> list[BrokerPosition]:
        return [self._to_position(p) for p in self._client.get_all_positions()]

    def get_position(self, symbol: str) -> BrokerPosition | None:
        try:
            return self._to_position(self._client.get_open_position(symbol))
        except self._sdk.APIError:
            return None

    def get_order_by_client_id(self, client_order_id: str) -> OrderResult | None:
        try:
            return self._to_order_result(self._client.get_order_by_client_id(client_order_id))
        except self._sdk.APIError:
            return None

    def submit_bracket_buy(self, order: BracketOrder) -> OrderResult:
        sdk = self._sdk
        take_profit = (
            sdk.TakeProfitRequest(limit_price=order.take_profit_price)
            if order.take_profit_price is not None
            else None
        )
        request = sdk.MarketOrderRequest(
            symbol=order.symbol,
            qty=order.qty,
            side=sdk.OrderSide.BUY,
            time_in_force=self._tif(order.time_in_force),
            order_class=sdk.OrderClass.BRACKET,
            take_profit=take_profit,
            stop_loss=sdk.StopLossRequest(stop_price=order.stop_loss_price),
            client_order_id=order.client_order_id,
        )
        return self._to_order_result(self._client.submit_order(request))

    def submit_moc_sell(self, symbol: str, qty: int, client_order_id: str) -> OrderResult:
        sdk = self._sdk
        request = sdk.MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=sdk.OrderSide.SELL,
            time_in_force=sdk.TimeInForce.CLS,  # market-on-close
            client_order_id=client_order_id,
        )
        return self._to_order_result(self._client.submit_order(request))

    def close_all_positions(self, *, cancel_orders: bool = True) -> list[OrderResult]:
        responses = self._client.close_all_positions(cancel_orders=cancel_orders)
        results: list[OrderResult] = []
        for item in responses or []:
            body = getattr(item, 'body', None)
            if body is not None:
                results.append(self._to_order_result(body))
        return results

    def cancel_all_orders(self) -> int:
        return len(self._client.cancel_orders() or [])

    # -- mapping helpers ----------------------------------------------------
    def _tif(self, time_in_force: str) -> Any:
        mapping = {
            'day': self._sdk.TimeInForce.DAY,
            'gtc': self._sdk.TimeInForce.GTC,
        }
        try:
            return mapping[time_in_force.lower()]
        except KeyError as exc:
            raise BrokerError(f'Unsupported time_in_force: {time_in_force!r}') from exc

    def _to_position(self, p: Any) -> BrokerPosition:
        return BrokerPosition(
            symbol=str(p.symbol),
            qty=_i(p.qty),
            avg_entry_price=_f(p.avg_entry_price) or 0.0,
            current_price=_f(getattr(p, 'current_price', None)),
            market_value=_f(getattr(p, 'market_value', None)),
            unrealized_pl=_f(getattr(p, 'unrealized_pl', None)),
            side=str(getattr(p, 'side', 'long')).rsplit('.', 1)[-1].lower(),
        )

    def _to_order_result(self, o: Any) -> OrderResult:
        side_text = str(getattr(o.side, 'value', o.side)).rsplit('.', 1)[-1].lower()
        return OrderResult(
            id=str(o.id),
            client_order_id=str(getattr(o, 'client_order_id', '') or ''),
            symbol=str(o.symbol),
            side=OrderSide.SELL if side_text == 'sell' else OrderSide.BUY,
            qty=_i(o.qty),
            status=_normalize_status(o.status),
            filled_qty=_i(getattr(o, 'filled_qty', 0) or 0),
            filled_avg_price=_f(getattr(o, 'filled_avg_price', None)),
            submitted_at=getattr(o, 'submitted_at', None),
        )
