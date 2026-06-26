"""Lambda entrypoint (DECISIONS.md §3, §4, §13 step 7).

EventBridge invokes ``handler`` on a coarse weekday/session schedule; the
**market-calendar gate inside the engine** makes it correct (holidays, half-days,
DST, weekend misfires → do nothing). The handler is deliberately thin:

    load secrets → build the engine (same wiring as local) → run once → log JSON

All knobs come from environment variables set by the CDK stack, so there is no
AWS-specific decision logic — only AWS-specific *wiring*.

Env vars:
    MODE                 'paper' (default) or 'live' — selects the Alpaca endpoint
    SECRET_SSM_PARAMS    comma-separated SSM SecureString names to load as env vars
    DYNAMODB_TABLE       state table (required in AWS for durable state)
    STRATEGY_CONFIG_SSM  SSM parameter holding the StrategyConfig JSON
    ALPACA_DATA_FEED     'iex' (default) or 'sip'
    OPENROUTER_MODEL     advisory model slug (default in build_engine)
    USE_ADVISOR          '0' to disable the LLM advisory pass (default on)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger('trading_bot.aws')
logger.setLevel(logging.INFO)


def handler(event: dict[str, Any] | None = None, context: Any = None) -> dict[str, Any]:
    """Run one trading-engine pass. Returns a JSON-serializable summary."""
    started = datetime.now(UTC)
    secret_params = os.environ.get('SECRET_SSM_PARAMS')
    if secret_params:
        from trading_bot.aws.secrets import load_ssm_secrets_into_env

        load_ssm_secrets_into_env([n.strip() for n in secret_params.split(',')])

    from trading_bot.broker import BrokerMode
    from trading_bot.runner import build_engine, run_once

    is_live = os.environ.get('MODE', 'paper').lower() == 'live'
    mode = BrokerMode.LIVE if is_live else BrokerMode.PAPER
    use_advisor = os.environ.get('USE_ADVISOR', '1') != '0'
    feed = os.environ.get('ALPACA_DATA_FEED', 'iex')

    engine, provider = build_engine(mode, use_advisor=use_advisor, feed=feed)
    record = run_once(engine, provider)

    duration_ms = (datetime.now(UTC) - started).total_seconds() * 1000.0
    summary = {
        'mode': mode.value,
        'trade_date': record.trade_date.isoformat(),
        'action': record.action,
        'reason': record.reason,
        'status_before': record.status_before.value,
        'status_after': record.status_after.value,
        'alert': record.alert,
        'duration_ms': round(duration_ms, 1),
    }
    # Structured single-line JSON → queryable in CloudWatch Logs Insights (§9).
    logger.info(json.dumps(summary))
    return summary
