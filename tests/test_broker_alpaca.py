"""Unit tests for the AlpacaBroker adapter's error translation.

alpaca-py is installed in the dev venv, so these construct a broker with a stub
client (``verify=False`` skips the startup account fetch) — no network.
"""

from __future__ import annotations

import pytest
from alpaca.common.exceptions import APIError

from trading_bot.broker.alpaca import AlpacaBroker, _is_duplicate_client_order_id
from trading_bot.broker.base import BracketOrder, BrokerMode
from trading_bot.broker.credentials import BrokerCredentials
from trading_bot.broker.errors import DuplicateClientOrderIdError

_DUPLICATE = APIError('{"code":40010001,"message":"client_order_id must be unique"}')
_OTHER = APIError('{"code":40010000,"message":"insufficient buying power"}')


class _RaisingClient:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def submit_order(self, request: object) -> object:
        raise self._exc


def _broker(exc: Exception) -> AlpacaBroker:
    broker = AlpacaBroker(
        BrokerMode.PAPER,
        BrokerCredentials(api_key='k', secret_key='s'),
        verify=False,
    )
    broker._client = _RaisingClient(exc)  # type: ignore[assignment]
    return broker


def _order() -> BracketOrder:
    return BracketOrder(
        symbol='QQQ',
        qty=1,
        stop_loss_price=390.0,
        take_profit_price=410.0,
        client_order_id='2026-06-23-ENTRY',
    )


def test_detect_duplicate_by_code_and_message() -> None:
    assert _is_duplicate_client_order_id(_DUPLICATE)
    assert not _is_duplicate_client_order_id(_OTHER)


def test_bracket_buy_translates_duplicate_to_typed_error() -> None:
    with pytest.raises(DuplicateClientOrderIdError):
        _broker(_DUPLICATE).submit_bracket_buy(_order())


def test_bracket_buy_reraises_other_api_errors() -> None:
    with pytest.raises(APIError):
        _broker(_OTHER).submit_bracket_buy(_order())
