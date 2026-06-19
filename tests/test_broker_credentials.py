"""Tests for credential loading from the environment."""

from __future__ import annotations

import pytest

from trading_bot.broker.base import BrokerMode
from trading_bot.broker.credentials import load_credentials
from trading_bot.broker.errors import BrokerNotConfiguredError


def test_mode_specific_keys_take_precedence() -> None:
    env = {
        'ALPACA_PAPER_API_KEY': 'paper-key',
        'ALPACA_PAPER_SECRET_KEY': 'paper-secret',
        'ALPACA_API_KEY': 'generic-key',
        'ALPACA_SECRET_KEY': 'generic-secret',
    }
    creds = load_credentials(BrokerMode.PAPER, env)
    assert creds.api_key == 'paper-key'
    assert creds.secret_key == 'paper-secret'


def test_falls_back_to_generic_keys() -> None:
    env = {'ALPACA_API_KEY': 'k', 'ALPACA_SECRET_KEY': 's'}
    creds = load_credentials(BrokerMode.LIVE, env)
    assert (creds.api_key, creds.secret_key) == ('k', 's')


def test_missing_credentials_raises_with_helpful_names() -> None:
    with pytest.raises(BrokerNotConfiguredError, match='ALPACA_LIVE_API_KEY'):
        load_credentials(BrokerMode.LIVE, {})
