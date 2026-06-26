#!/usr/bin/env python3
"""CDK app entrypoint (DECISIONS.md §13 step 7).

Synthesizes the **paper** (staging) stack by default; add ``--context live=1`` to
also synthesize the **live** stack. Each reads the same ``StrategyConfig`` JSON
that the local runner uses (``examples/strategy_config.example.json``), so the
deployed config matches what you tested locally — point it at your tuned file via
the ``STRATEGY_CONFIG_FILE`` env var if you keep one elsewhere.

Deploy:
    cd infra
    uv run --extra infra cdk deploy TradingBotPaper      # staging
    uv run --extra infra cdk deploy TradingBotLive -c live=1
"""

from __future__ import annotations

import os
from pathlib import Path

import aws_cdk as cdk
from trading_bot_infra.stack import TradingBotStack

REPO_ROOT = Path(__file__).resolve().parent.parent


def _strategy_config_json() -> str:
    path = os.environ.get('STRATEGY_CONFIG_FILE') or str(
        REPO_ROOT / 'examples' / 'strategy_config.example.json'
    )
    return Path(path).read_text()


def main() -> None:
    app = cdk.App()
    config_json = _strategy_config_json()
    openrouter_model = os.environ.get('OPENROUTER_MODEL', 'openai/gpt-4o-mini')
    alarm_email = os.environ.get('ALARM_EMAIL')
    env = cdk.Environment(
        account=os.environ.get('CDK_DEFAULT_ACCOUNT'),
        region=os.environ.get('CDK_DEFAULT_REGION'),
    )

    TradingBotStack(
        app,
        'TradingBotPaper',
        mode='paper',
        repo_root=REPO_ROOT,
        strategy_config_json=config_json,
        openrouter_model=openrouter_model,
        data_feed=os.environ.get('ALPACA_DATA_FEED', 'iex'),
        alarm_email=alarm_email,
        env=env,
    )

    if app.node.try_get_context('live'):
        TradingBotStack(
            app,
            'TradingBotLive',
            mode='live',
            repo_root=REPO_ROOT,
            strategy_config_json=config_json,
            openrouter_model=openrouter_model,
            data_feed=os.environ.get('ALPACA_DATA_FEED', 'sip'),
            alarm_email=alarm_email,
            env=env,
        )

    app.synth()


if __name__ == '__main__':
    main()
