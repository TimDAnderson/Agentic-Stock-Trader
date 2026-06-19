"""Tests for the in-memory FakeBroker and the order flow it models."""

from __future__ import annotations

from datetime import date

import pytest

from trading_bot.broker import (
    BracketOrder,
    BrokerMode,
    DuplicateClientOrderIdError,
    FakeBroker,
    OrderSide,
    OrderStatus,
    entry_id,
    moc_id,
)


def _broker() -> FakeBroker:
    return FakeBroker(BrokerMode.PAPER, equity=100_000.0, prices={'QQQ': 400.0})


def test_account_reports_mode_and_equity() -> None:
    acct = _broker().get_account()
    assert acct.mode is BrokerMode.PAPER
    assert acct.equity == 100_000.0


def test_bracket_buy_opens_position_and_fills() -> None:
    b = _broker()
    order = BracketOrder(
        symbol='QQQ',
        qty=10,
        stop_loss_price=395.0,
        take_profit_price=410.0,
        client_order_id=entry_id(date(2026, 6, 2)),
    )
    result = b.submit_bracket_buy(order)
    assert result.side is OrderSide.BUY
    assert result.status is OrderStatus.FILLED
    assert result.filled_qty == 10
    assert result.filled_avg_price == 400.0

    pos = b.get_position('QQQ')
    assert pos is not None
    assert pos.qty == 10 and pos.avg_entry_price == 400.0


def test_duplicate_client_order_id_is_rejected() -> None:
    b = _broker()
    order = BracketOrder(
        symbol='QQQ', qty=10, stop_loss_price=395.0, client_order_id=entry_id(date(2026, 6, 2))
    )
    b.submit_bracket_buy(order)
    # A retried/overlapping run submits the same id -> rejected (idempotency).
    with pytest.raises(DuplicateClientOrderIdError):
        b.submit_bracket_buy(order)


def test_lookup_by_client_id_enables_reconciliation() -> None:
    b = _broker()
    coid = entry_id(date(2026, 6, 2))
    assert b.get_order_by_client_id(coid) is None
    b.submit_bracket_buy(
        BracketOrder(symbol='QQQ', qty=5, stop_loss_price=395.0, client_order_id=coid)
    )
    found = b.get_order_by_client_id(coid)
    assert found is not None and found.qty == 5


def test_position_marks_to_current_price() -> None:
    b = _broker()
    b.submit_bracket_buy(
        BracketOrder(
            symbol='QQQ', qty=10, stop_loss_price=395.0, client_order_id=entry_id(date(2026, 6, 2))
        )
    )
    b.set_price('QQQ', 405.0)
    pos = b.get_position('QQQ')
    assert pos is not None
    assert pos.current_price == 405.0
    assert pos.unrealized_pl == pytest.approx(50.0)  # (405 - 400) * 10


def test_close_all_positions_liquidates() -> None:
    b = _broker()
    b.submit_bracket_buy(
        BracketOrder(
            symbol='QQQ', qty=10, stop_loss_price=395.0, client_order_id=entry_id(date(2026, 6, 2))
        )
    )
    results = b.close_all_positions(cancel_orders=True)
    assert len(results) == 1
    assert results[0].side is OrderSide.SELL
    assert b.get_positions() == []


def test_moc_sell_flattens_the_position() -> None:
    b = _broker()
    b.submit_bracket_buy(
        BracketOrder(
            symbol='QQQ', qty=10, stop_loss_price=395.0, client_order_id=entry_id(date(2026, 6, 2))
        )
    )
    result = b.submit_moc_sell('QQQ', 10, moc_id(date(2026, 6, 2)))
    assert result.side is OrderSide.SELL
    assert b.get_position('QQQ') is None


def test_fake_broker_satisfies_broker_protocol() -> None:
    from trading_bot.broker.base import Broker

    assert isinstance(_broker(), Broker)
