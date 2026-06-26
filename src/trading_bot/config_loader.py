"""Resolve a ``StrategyConfig`` from one source, the same way everywhere.

The decision logic is a pure function of ``(MarketState, StrategyConfig)``, so the
*only* thing that should make a local run decide differently from the deployed
Lambda is the config it loads. This module is that single seam: local runs and
AWS read config through the same resolver, just pointed at a different source.

Precedence (first match wins), so a local file can override a remote default:
  1. an explicit ``file`` / ``ssm_parameter`` argument
  2. ``STRATEGY_CONFIG_FILE`` env var — a local JSON (``.json``) or YAML file
  3. ``STRATEGY_CONFIG_SSM`` env var — an SSM parameter holding a JSON object
  4. built-in ``StrategyConfig()`` defaults

JSON uses the stdlib; YAML needs PyYAML (optional); SSM needs boto3 (``aws``
extra). All are imported lazily so the common JSON/defaults path has no deps.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from trading_bot.domain.config import StrategyConfig

ENV_CONFIG_FILE = 'STRATEGY_CONFIG_FILE'
ENV_CONFIG_SSM = 'STRATEGY_CONFIG_SSM'


class ConfigError(Exception):
    """A configured source could not be read or parsed into a config object."""


def load_strategy_config(
    *,
    file: str | None = None,
    ssm_parameter: str | None = None,
    region_name: str | None = None,
) -> StrategyConfig:
    """Load config from the first available source, else defaults.

    See the module docstring for precedence. Returns a validated
    ``StrategyConfig`` (``"HH:MM"`` strings parse to ``time``; unknown keys are
    ignored), so callers get the same object regardless of source.
    """
    file = file or os.environ.get(ENV_CONFIG_FILE)
    ssm_parameter = ssm_parameter or os.environ.get(ENV_CONFIG_SSM)

    if file:
        return StrategyConfig.from_dict(_read_file(file))
    if ssm_parameter:
        return StrategyConfig.from_dict(_read_ssm(ssm_parameter, region_name))
    return StrategyConfig()


def describe_source(*, file: str | None = None, ssm_parameter: str | None = None) -> str:
    """Human-readable label for which source ``load_strategy_config`` will use."""
    file = file or os.environ.get(ENV_CONFIG_FILE)
    ssm_parameter = ssm_parameter or os.environ.get(ENV_CONFIG_SSM)
    if file:
        return f'file:{file}'
    if ssm_parameter:
        return f'ssm:{ssm_parameter}'
    return 'defaults'


def _read_file(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f'Config file not found: {path}')
    text = p.read_text()
    data = _parse_yaml(text) if p.suffix.lower() in ('.yaml', '.yml') else json.loads(text)
    if not isinstance(data, dict):
        raise ConfigError(
            f'Config in {path} must be a JSON/YAML object, got {type(data).__name__}.'
        )
    return data


def _parse_yaml(text: str) -> Any:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - exercised only without PyYAML
        raise ConfigError(
            'YAML config requires PyYAML. Install it (`uv add pyyaml`) or use a .json file.'
        ) from exc
    return yaml.safe_load(text)


def _read_ssm(name: str, region_name: str | None) -> dict[str, Any]:
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ConfigError(
            'SSM config requires boto3. Install the aws extra: `uv sync --extra aws`.'
        ) from exc
    client = boto3.client('ssm', region_name=region_name or os.environ.get('AWS_DEFAULT_REGION'))
    value = client.get_parameter(Name=name, WithDecryption=True)['Parameter']['Value']
    data = json.loads(value)
    if not isinstance(data, dict):
        raise ConfigError(f'SSM parameter {name} must hold a JSON object.')
    return data
