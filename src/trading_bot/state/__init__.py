"""Durable state machine + decision/trade system-of-record (DECISIONS.md §4, §9).

The engine depends on the ``StateRepository`` Protocol. ``InMemoryStateRepository``
backs tests/local; ``DynamoStateRepository`` is the deployed implementation
(requires the ``aws`` extra). ``reconcile`` holds the broker-vs-DB safety rules.
"""

from trading_bot.state.errors import (
    ConcurrentTransitionError,
    StateAlreadyExistsError,
    StateError,
)
from trading_bot.state.memory import InMemoryStateRepository
from trading_bot.state.models import (
    DailyState,
    PositionStatus,
    RunRecord,
    TradeRecord,
)
from trading_bot.state.reconcile import ReconcileAction, ReconcileResult, reconcile
from trading_bot.state.repository import StateRepository

__all__ = [
    'ConcurrentTransitionError',
    'StateAlreadyExistsError',
    'StateError',
    'InMemoryStateRepository',
    'DailyState',
    'PositionStatus',
    'RunRecord',
    'TradeRecord',
    'ReconcileAction',
    'ReconcileResult',
    'reconcile',
    'StateRepository',
]
