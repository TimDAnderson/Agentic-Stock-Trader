"""Broker-layer exceptions.

A small, explicit hierarchy so callers can distinguish "the broker said no"
(e.g. a duplicate order — safe to treat as already-done) from "we're
misconfigured" (e.g. wrong mode/keys — must fail loudly, never fall through).
"""

from __future__ import annotations


class BrokerError(Exception):
    """Base class for all broker-layer failures."""


class BrokerNotConfiguredError(BrokerError):
    """Missing/invalid configuration — credentials absent, alpaca-py not installed."""


class AccountModeMismatchError(BrokerError):
    """The connected account doesn't match the expected paper/live mode.

    Raised at startup before any order is placed (DECISIONS.md §2): never fall
    through to live when paper was intended.
    """


class DuplicateClientOrderIdError(BrokerError):
    """An order with this ``client_order_id`` already exists.

    The date-keyed id makes entries idempotent (DECISIONS.md §4); a duplicate
    means the entry already happened, so the safe response is "do not re-order".
    """
