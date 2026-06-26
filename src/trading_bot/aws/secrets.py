"""Load secrets from SSM Parameter Store into the environment (DECISIONS.md §3).

Secrets are **SecureString** parameters under ``/trading-bot/<mode>/`` (created and
rotated by you in the console/CLI — never in code/CloudFormation). Each parameter
name's last path segment is the env-var name the code already reads
(``/trading-bot/paper/ALPACA_PAPER_API_KEY`` → ``ALPACA_PAPER_API_KEY``). The
handler loads them once at cold start; from there ``load_credentials`` and
``OpenRouterLLM`` find their keys exactly as they do locally — so secret loading
is the *only* thing that differs between laptop and Lambda.

boto3 is imported lazily (``aws`` extra). By default we do **not** clobber a
value already set in the environment, so a local override still wins.
"""

from __future__ import annotations

import os
from typing import Any

_MAX_BATCH = 10  # ssm:GetParameters caps Names at 10 per call


class SecretsError(Exception):
    """A secret parameter could not be fetched or decrypted."""


def load_ssm_secrets_into_env(
    parameter_names: list[str],
    *,
    region_name: str | None = None,
    override: bool = False,
) -> list[str]:
    """Fetch each SecureString in ``parameter_names`` and set it as an env var.

    The env-var name is the parameter's last path segment. Returns the list of
    names that were set. Existing values are kept unless ``override`` is true.
    Raises ``SecretsError`` if any parameter is missing so a misconfigured deploy
    fails loudly rather than trading blind.
    """
    names = [n for n in parameter_names if n]
    if not names:
        return []
    values = _fetch_ssm_parameters(names, region_name)

    applied: list[str] = []
    for name, value in values.items():
        env_key = name.rsplit('/', 1)[-1]
        if not override and os.environ.get(env_key):
            continue
        os.environ[env_key] = value
        applied.append(env_key)
    return applied


def _fetch_ssm_parameters(names: list[str], region_name: str | None) -> dict[str, str]:
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - only without the aws extra
        raise SecretsError(
            'boto3 is not installed. Install the aws extra: `uv sync --extra aws`.'
        ) from exc
    region = region_name or os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION')
    client = boto3.client('ssm', region_name=region)

    out: dict[str, str] = {}
    for start in range(0, len(names), _MAX_BATCH):
        batch = names[start : start + _MAX_BATCH]
        try:
            resp: dict[str, Any] = client.get_parameters(Names=batch, WithDecryption=True)
        except Exception as exc:  # boto/botocore errors — surface as our typed error
            raise SecretsError(f'Could not fetch SSM parameters {batch}: {exc}') from exc
        invalid = resp.get('InvalidParameters') or []
        if invalid:
            raise SecretsError(f'SSM parameters not found (or no access): {invalid}')
        for param in resp.get('Parameters', []):
            out[str(param['Name'])] = str(param['Value'])
    return out
