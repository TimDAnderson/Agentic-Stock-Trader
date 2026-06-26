"""CDK assertion tests for the trading-bot stack.

Skipped unless the ``infra`` extra (aws-cdk-lib) is installed. Validates that the
synthesized template has the resources the bot depends on — in particular the
DynamoDB schema must match ``DynamoStateRepository`` (PK/SK + GSI1).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip('aws_cdk')

import aws_cdk as cdk  # noqa: E402
from aws_cdk.assertions import Match, Template  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / 'infra'))

from trading_bot_infra.stack import TradingBotStack  # noqa: E402

_CONFIG_JSON = '{"version": "v1-test", "target_position_usd": 500.0}'


def _template(mode: str, tmp_path: Path) -> Template:
    app = cdk.App(outdir=str(tmp_path))
    stack = TradingBotStack(
        app,
        f'TradingBot{mode.title()}',
        mode=mode,
        repo_root=REPO_ROOT,
        strategy_config_json=_CONFIG_JSON,
    )
    return Template.from_stack(stack)


def test_core_resource_counts(tmp_path: Path) -> None:
    t = _template('paper', tmp_path)
    t.resource_count_is('AWS::DynamoDB::Table', 1)
    t.resource_count_is('AWS::Lambda::Function', 1)
    # Secrets live in SSM SecureStrings created out-of-band — the stack makes none.
    t.resource_count_is('AWS::SecretsManager::Secret', 0)
    t.resource_count_is('AWS::SSM::Parameter', 1)  # only the non-secret StrategyConfig
    t.resource_count_is('AWS::Events::Rule', 3)  # session + MOC backstop + EOD
    t.resource_count_is('AWS::CloudWatch::Alarm', 2)


def test_grants_ssm_secret_read_and_kms_decrypt(tmp_path: Path) -> None:
    t = _template('paper', tmp_path)
    # The Lambda role policy must allow reading the SecureString params + KMS decrypt.
    t.has_resource_properties(
        'AWS::IAM::Policy',
        {
            'PolicyDocument': {
                'Statement': Match.array_with(
                    [
                        Match.object_like({'Action': ['ssm:GetParameter', 'ssm:GetParameters']}),
                        Match.object_like({'Action': 'kms:Decrypt'}),
                    ]
                )
            }
        },
    )


def test_dynamo_schema_matches_repository(tmp_path: Path) -> None:
    t = _template('paper', tmp_path)
    t.has_resource_properties(
        'AWS::DynamoDB::Table',
        {
            'BillingMode': 'PAY_PER_REQUEST',
            'KeySchema': [
                {'AttributeName': 'PK', 'KeyType': 'HASH'},
                {'AttributeName': 'SK', 'KeyType': 'RANGE'},
            ],
            'GlobalSecondaryIndexes': Match.array_with(
                [
                    Match.object_like(
                        {
                            'IndexName': 'GSI1',
                            'KeySchema': [
                                {'AttributeName': 'GSI1PK', 'KeyType': 'HASH'},
                                {'AttributeName': 'GSI1SK', 'KeyType': 'RANGE'},
                            ],
                        }
                    )
                ]
            ),
        },
    )


def test_lambda_wired_to_state_and_secret(tmp_path: Path) -> None:
    t = _template('paper', tmp_path)
    t.has_resource_properties(
        'AWS::Lambda::Function',
        {
            'PackageType': 'Image',
            'Environment': {
                'Variables': Match.object_like(
                    {
                        'MODE': 'paper',
                        'USE_ADVISOR': '1',
                        'ALPACA_DATA_FEED': 'iex',
                        'SECRET_SSM_PARAMS': (
                            '/trading-bot/paper/ALPACA_PAPER_API_KEY,'
                            '/trading-bot/paper/ALPACA_PAPER_SECRET_KEY,'
                            '/trading-bot/paper/OPENROUTER_API_KEY'
                        ),
                    }
                )
            },
        },
    )


def test_live_stack_retains_table(tmp_path: Path) -> None:
    t = _template('live', tmp_path)
    t.has_resource('AWS::DynamoDB::Table', {'DeletionPolicy': 'Retain'})


def test_invalid_mode_rejected(tmp_path: Path) -> None:
    app = cdk.App(outdir=str(tmp_path))
    with pytest.raises(ValueError, match='mode must be'):
        TradingBotStack(
            app, 'Bad', mode='staging', repo_root=REPO_ROOT, strategy_config_json=_CONFIG_JSON
        )
