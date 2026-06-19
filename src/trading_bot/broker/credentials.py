"""Broker credential loading (DECISIONS.md §2, §11).

Secrets come from environment variables locally (and, later, AWS Secrets
Manager / SSM in deployment — same ``BrokerCredentials`` type, different loader).
Keys are **not interchangeable** between paper and live, so we look up
mode-specific names first and fall back to the generic pair.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

from trading_bot.broker.base import BrokerMode
from trading_bot.broker.errors import BrokerNotConfiguredError


class BrokerCredentials(BaseModel):
    model_config = ConfigDict(frozen=True)

    api_key: str
    secret_key: str


def _first_set(env: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = env.get(name)
        if value:
            return value
    return None


def load_credentials(mode: BrokerMode, env: Mapping[str, str] | None = None) -> BrokerCredentials:
    """Load credentials for ``mode`` from the environment.

    Looks for ``ALPACA_<MODE>_API_KEY`` / ``..._SECRET_KEY`` first, then the
    generic ``ALPACA_API_KEY`` / ``ALPACA_SECRET_KEY``. Raises
    ``BrokerNotConfiguredError`` with the names it checked if either is missing.
    """
    env = os.environ if env is None else env
    prefix = mode.value.upper()
    api_names = (f'ALPACA_{prefix}_API_KEY', 'ALPACA_API_KEY')
    secret_names = (f'ALPACA_{prefix}_SECRET_KEY', 'ALPACA_SECRET_KEY')

    api_key = _first_set(env, *api_names)
    secret_key = _first_set(env, *secret_names)
    if not api_key or not secret_key:
        missing = api_names if not api_key else secret_names
        raise BrokerNotConfiguredError(
            f'Missing Alpaca credentials for {mode.value} mode; set one of {list(missing)}.'
        )
    return BrokerCredentials(api_key=api_key, secret_key=secret_key)
