"""Date-keyed idempotent ``client_order_id`` helpers (DECISIONS.md §4).

A deterministic id per (trade_date, kind) means a retried/overlapping run
submits the *same* id — the broker rejects the duplicate, which is defense in
depth alongside the DynamoDB conditional write. Ids look like ``2026-06-02-ENTRY``.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum


class OrderKind(StrEnum):
    ENTRY = 'ENTRY'
    EXIT = 'EXIT'
    MOC = 'MOC'  # market-on-close backstop


def client_order_id(trade_date: date, kind: OrderKind) -> str:
    return f'{trade_date.isoformat()}-{kind.value}'


def entry_id(trade_date: date) -> str:
    return client_order_id(trade_date, OrderKind.ENTRY)


def exit_id(trade_date: date) -> str:
    return client_order_id(trade_date, OrderKind.EXIT)


def moc_id(trade_date: date) -> str:
    return client_order_id(trade_date, OrderKind.MOC)
