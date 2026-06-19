"""Broker layer (DECISIONS.md §2, §5).

The system depends on the ``Broker`` Protocol and the typed value objects here,
not on any SDK. ``FakeBroker`` is the in-memory double for tests/local dev;
``AlpacaBroker`` is the real paper/live adapter (requires the ``broker`` extra).
"""

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
from trading_bot.broker.credentials import BrokerCredentials, load_credentials
from trading_bot.broker.errors import (
    AccountModeMismatchError,
    BrokerError,
    BrokerNotConfiguredError,
    DuplicateClientOrderIdError,
)
from trading_bot.broker.fake import FakeBroker
from trading_bot.broker.ids import OrderKind, client_order_id, entry_id, exit_id, moc_id

__all__ = [
    'Account',
    'BracketOrder',
    'Broker',
    'BrokerMode',
    'BrokerPosition',
    'OrderResult',
    'OrderSide',
    'OrderStatus',
    'BrokerCredentials',
    'load_credentials',
    'AccountModeMismatchError',
    'BrokerError',
    'BrokerNotConfiguredError',
    'DuplicateClientOrderIdError',
    'FakeBroker',
    'OrderKind',
    'client_order_id',
    'entry_id',
    'exit_id',
    'moc_id',
]
