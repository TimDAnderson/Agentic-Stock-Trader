"""Tests for the SSM SecureString → environment loader (no network)."""

from __future__ import annotations

import os

import pytest

from trading_bot.aws import secrets
from trading_bot.aws.secrets import SecretsError, load_ssm_secrets_into_env

_PARAMS = [
    '/trading-bot/paper/ALPACA_PAPER_API_KEY',
    '/trading-bot/paper/OPENROUTER_API_KEY',
]


def _stub_fetch(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    monkeypatch.setattr(secrets, '_fetch_ssm_parameters', lambda names, region: values)


def test_sets_env_from_last_path_segment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('ALPACA_PAPER_API_KEY', raising=False)
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    _stub_fetch(
        monkeypatch,
        {
            '/trading-bot/paper/ALPACA_PAPER_API_KEY': 'k',
            '/trading-bot/paper/OPENROUTER_API_KEY': 'o',
        },
    )
    applied = load_ssm_secrets_into_env(_PARAMS)
    assert set(applied) == {'ALPACA_PAPER_API_KEY', 'OPENROUTER_API_KEY'}
    assert os.environ['ALPACA_PAPER_API_KEY'] == 'k'
    assert os.environ['OPENROUTER_API_KEY'] == 'o'


def test_does_not_clobber_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('ALPACA_PAPER_API_KEY', 'local')
    _stub_fetch(monkeypatch, {'/trading-bot/paper/ALPACA_PAPER_API_KEY': 'remote'})
    applied = load_ssm_secrets_into_env(['/trading-bot/paper/ALPACA_PAPER_API_KEY'])
    assert os.environ['ALPACA_PAPER_API_KEY'] == 'local'  # local wins
    assert applied == []


def test_override_replaces_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('ALPACA_PAPER_API_KEY', 'local')
    _stub_fetch(monkeypatch, {'/trading-bot/paper/ALPACA_PAPER_API_KEY': 'remote'})
    load_ssm_secrets_into_env(['/trading-bot/paper/ALPACA_PAPER_API_KEY'], override=True)
    assert os.environ['ALPACA_PAPER_API_KEY'] == 'remote'


def test_empty_list_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    # No fetch should happen for an empty/blank list.
    monkeypatch.setattr(
        secrets,
        '_fetch_ssm_parameters',
        lambda names, region: pytest.fail('should not fetch'),
    )
    assert load_ssm_secrets_into_env(['', '  '.strip()]) == []


def test_missing_parameter_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(names: list[str], region: str | None) -> dict[str, str]:
        raise SecretsError('SSM parameters not found (or no access): [...]')

    monkeypatch.setattr(secrets, '_fetch_ssm_parameters', _raise)
    with pytest.raises(SecretsError, match='not found'):
        load_ssm_secrets_into_env(_PARAMS)
