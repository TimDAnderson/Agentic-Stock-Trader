"""Tests for the Lambda handler wiring (no network, no boto3 calls)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from trading_bot import runner
from trading_bot.aws import handler as handler_mod
from trading_bot.broker.base import BrokerMode
from trading_bot.state.models import PositionStatus, RunRecord


def _run_record() -> RunRecord:
    return RunRecord(
        trade_date=datetime(2026, 6, 23).date(),
        ts=datetime(2026, 6, 23, 18, tzinfo=UTC),
        action='DO_NOTHING',
        reason='Mixed signals.',
        status_before=PositionStatus.NO_POSITION,
        status_after=PositionStatus.NO_POSITION,
        strategy_version='v1',
        mode=BrokerMode.PAPER,
    )


@pytest.fixture
def _patched(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    captured: dict[str, object] = {}

    def fake_build_engine(mode: object, **kwargs: object) -> tuple[object, object]:
        captured['mode'] = mode
        captured['kwargs'] = kwargs
        return object(), object()

    def fake_run_once(engine: object, provider: object) -> RunRecord:
        captured['ran'] = True
        return _run_record()

    monkeypatch.setattr(runner, 'build_engine', fake_build_engine)
    monkeypatch.setattr(runner, 'run_once', fake_run_once)
    monkeypatch.delenv('SECRET_SSM_PARAMS', raising=False)
    return captured


def test_handler_runs_and_summarizes(_patched: dict[str, object]) -> None:
    summary = handler_mod.handler({}, None)
    assert _patched['ran'] is True
    assert summary['mode'] == 'paper'
    assert summary['action'] == 'DO_NOTHING'
    assert summary['status_after'] == 'NO_POSITION'
    assert 'duration_ms' in summary


def test_handler_selects_live_mode(
    monkeypatch: pytest.MonkeyPatch, _patched: dict[str, object]
) -> None:
    monkeypatch.setenv('MODE', 'live')
    handler_mod.handler({}, None)
    assert _patched['mode'] is BrokerMode.LIVE


def test_handler_honors_use_advisor_and_feed(
    monkeypatch: pytest.MonkeyPatch, _patched: dict[str, object]
) -> None:
    monkeypatch.setenv('USE_ADVISOR', '0')
    monkeypatch.setenv('ALPACA_DATA_FEED', 'sip')
    handler_mod.handler({}, None)
    kwargs = _patched['kwargs']
    assert isinstance(kwargs, dict)
    assert kwargs['use_advisor'] is False
    assert kwargs['feed'] == 'sip'


def test_handler_loads_secrets_when_configured(
    monkeypatch: pytest.MonkeyPatch, _patched: dict[str, object]
) -> None:
    calls: list[list[str]] = []
    from trading_bot.aws import secrets

    monkeypatch.setattr(secrets, 'load_ssm_secrets_into_env', lambda names: calls.append(names))
    monkeypatch.setenv(
        'SECRET_SSM_PARAMS',
        '/trading-bot/paper/ALPACA_PAPER_API_KEY, /trading-bot/paper/OPENROUTER_API_KEY',
    )
    handler_mod.handler({}, None)
    assert calls == [
        ['/trading-bot/paper/ALPACA_PAPER_API_KEY', '/trading-bot/paper/OPENROUTER_API_KEY']
    ]
