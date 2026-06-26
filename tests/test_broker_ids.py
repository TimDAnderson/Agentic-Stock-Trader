"""Tests for date-keyed idempotent client_order_ids."""

from __future__ import annotations

from datetime import date

from trading_bot.broker.ids import OrderKind, client_order_id, entry_id, exit_id, moc_id


def test_ids_are_date_keyed_and_stable() -> None:
    d = date(2026, 6, 2)
    assert entry_id(d) == '2026-06-02-ENTRY'
    assert exit_id(d) == '2026-06-02-EXIT'
    assert moc_id(d) == '2026-06-02-MOC'
    # Deterministic: same inputs -> same id (the whole point of idempotency).
    assert entry_id(d) == client_order_id(d, OrderKind.ENTRY)


def test_ids_differ_by_day_and_kind() -> None:
    d1, d2 = date(2026, 6, 2), date(2026, 6, 3)
    assert entry_id(d1) != entry_id(d2)
    assert entry_id(d1) != exit_id(d1)
