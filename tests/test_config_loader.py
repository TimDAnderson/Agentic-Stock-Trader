"""Tests for the shared StrategyConfig resolver (local file / SSM / defaults)."""

from __future__ import annotations

import json
from datetime import time
from pathlib import Path

import pytest

from trading_bot.config_loader import (
    ENV_CONFIG_FILE,
    ConfigError,
    describe_source,
    load_strategy_config,
)


def test_defaults_when_no_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_CONFIG_FILE, raising=False)
    cfg = load_strategy_config()
    assert cfg.version == 'v1'
    assert cfg.target_position_usd is None
    assert describe_source() == 'defaults'


def test_loads_json_file(tmp_path: Path) -> None:
    path = tmp_path / 'cfg.json'
    path.write_text(
        json.dumps(
            {
                'version': 'v1-test',
                'target_position_usd': 500.0,
                'no_entry_after': '13:00',
                'unknown_key': 'ignored',
            }
        )
    )
    cfg = load_strategy_config(file=str(path))
    assert cfg.version == 'v1-test'
    assert cfg.target_position_usd == 500.0
    assert cfg.no_entry_after == time(13, 0)  # "HH:MM" parsed
    assert describe_source(file=str(path)) == f'file:{path}'


def test_explicit_file_overrides_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / 'env.json'
    env_file.write_text(json.dumps({'version': 'from-env'}))
    arg_file = tmp_path / 'arg.json'
    arg_file.write_text(json.dumps({'version': 'from-arg'}))
    monkeypatch.setenv(ENV_CONFIG_FILE, str(env_file))
    assert load_strategy_config(file=str(arg_file)).version == 'from-arg'
    assert load_strategy_config().version == 'from-env'  # falls back to env


def test_env_file_is_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / 'cfg.json'
    path.write_text(json.dumps({'max_position_pct': 0.25}))
    monkeypatch.setenv(ENV_CONFIG_FILE, str(path))
    assert load_strategy_config().max_position_pct == 0.25


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match='not found'):
        load_strategy_config(file=str(tmp_path / 'nope.json'))


def test_non_object_json_raises(tmp_path: Path) -> None:
    path = tmp_path / 'bad.json'
    path.write_text('[1, 2, 3]')
    with pytest.raises(ConfigError, match='must be a JSON/YAML object'):
        load_strategy_config(file=str(path))


def test_loads_yaml_file(tmp_path: Path) -> None:
    pytest.importorskip('yaml')
    path = tmp_path / 'cfg.yaml'
    path.write_text('version: v1-yaml\ntarget_position_usd: 750.0\nforce_exit_after: "15:50"\n')
    cfg = load_strategy_config(file=str(path))
    assert cfg.version == 'v1-yaml'
    assert cfg.target_position_usd == 750.0
    assert cfg.force_exit_after == time(15, 50)


def test_example_config_file_is_valid() -> None:
    example = Path(__file__).resolve().parents[1] / 'examples' / 'strategy_config.example.json'
    cfg = load_strategy_config(file=str(example))
    assert cfg.target_position_usd == 10000.0
    assert cfg.min_relative_volume == 0.3
    assert cfg.min_stop_loss_pct == 0.01
    assert cfg.instruments.bearish_symbol == 'PSQ'
