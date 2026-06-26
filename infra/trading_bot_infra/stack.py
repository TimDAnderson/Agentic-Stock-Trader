"""The parameterized trading-bot stack (DECISIONS.md §3, §9, §13 step 7).

One class, instantiated once per environment (``paper`` = staging, ``live`` =
prod), ideally in separate accounts for blast-radius isolation. It provisions:

- a **DynamoDB** single table (PK/SK + ``GSI1``) matching ``DynamoStateRepository``;
- IAM grants so the Lambda can read the **SSM SecureString** secrets you created
  out-of-band under ``/trading-bot/<mode>/`` (values live in Parameter Store,
  never in code/CloudFormation); the stack does **not** create those parameters;
- an **SSM** parameter holding the (non-secret) ``StrategyConfig`` JSON;
- a **container-image Lambda** running ``trading_bot.aws.handler.handler``;
- **EventBridge** rules: a coarse weekday/session cadence plus a guaranteed
  near-close liquidation fire (the in-handler calendar gate makes both correct);
- **CloudWatch** alarms (errors, near-timeout duration) → an SNS topic.
"""

from __future__ import annotations

from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_cloudwatch as cloudwatch,
)
from aws_cdk import (
    aws_cloudwatch_actions as cw_actions,
)
from aws_cdk import (
    aws_dynamodb as dynamodb,
)
from aws_cdk import (
    aws_ecr_assets as ecr_assets,
)
from aws_cdk import (
    aws_events as events,
)
from aws_cdk import (
    aws_events_targets as targets,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_sns as sns,
)
from aws_cdk import (
    aws_sns_subscriptions as subscriptions,
)
from aws_cdk import (
    aws_ssm as ssm,
)
from constructs import Construct


class TradingBotStack(Stack):
    """All resources for one mode (paper or live)."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        mode: str,
        repo_root: Path,
        strategy_config_json: str,
        openrouter_model: str = 'openai/gpt-4o-mini',
        data_feed: str = 'iex',
        alarm_email: str | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        if mode not in ('paper', 'live'):
            raise ValueError(f"mode must be 'paper' or 'live', got {mode!r}")
        is_live = mode == 'live'

        # --- DynamoDB single table (schema mirrors DynamoStateRepository) -----
        table = dynamodb.Table(
            self,
            'StateTable',
            partition_key=dynamodb.Attribute(name='PK', type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name='SK', type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            # Staging is disposable; prod state must survive a stack replace.
            removal_policy=RemovalPolicy.RETAIN if is_live else RemovalPolicy.DESTROY,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=is_live
            ),
        )
        table.add_global_secondary_index(
            index_name='GSI1',
            partition_key=dynamodb.Attribute(name='GSI1PK', type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name='GSI1SK', type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # --- SSM SecureString secrets (created out-of-band; we only read them) -
        # You create/rotate these in Parameter Store; their last path segment is
        # the env-var name the code reads. The Lambda is granted read + KMS
        # decrypt; the stack never sees or stores the values.
        key_prefix = 'PAPER' if not is_live else 'LIVE'
        secret_env_names = (
            f'ALPACA_{key_prefix}_API_KEY',
            f'ALPACA_{key_prefix}_SECRET_KEY',
            'OPENROUTER_API_KEY',
        )
        secret_param_names = [f'/trading-bot/{mode}/{name}' for name in secret_env_names]
        secret_param_arns = [
            self.format_arn(service='ssm', resource='parameter', resource_name=name.lstrip('/'))
            for name in secret_param_names
        ]

        # --- SSM: the (non-secret) StrategyConfig, versioned in git -----------
        config_param = ssm.StringParameter(
            self,
            'StrategyConfig',
            parameter_name=f'/trading-bot/{mode}/strategy-config',
            string_value=strategy_config_json,
            description='StrategyConfig JSON (non-secret). Zero-deploy tuning lever (§10).',
        )

        # --- Container-image Lambda (LangGraph deps exceed the zip limit) -----
        # Pin the build to amd64 so it deploys from any host (arm Macs build via
        # emulation); switch both to ARM_64 for cheaper Graviton if you prefer.
        log_group = logs.LogGroup(
            self,
            'FnLogs',
            retention=logs.RetentionDays.THREE_MONTHS,
            removal_policy=RemovalPolicy.RETAIN if is_live else RemovalPolicy.DESTROY,
        )
        fn = lambda_.DockerImageFunction(
            self,
            'Fn',
            code=lambda_.DockerImageCode.from_image_asset(
                directory=str(repo_root),
                platform=ecr_assets.Platform.LINUX_AMD64,
            ),
            architecture=lambda_.Architecture.X86_64,
            memory_size=1024,
            timeout=Duration.minutes(5),  # < the 15-min cap; reasoning is bounded (§7)
            environment={
                'MODE': mode,
                'SECRET_SSM_PARAMS': ','.join(secret_param_names),
                'DYNAMODB_TABLE': table.table_name,
                'STRATEGY_CONFIG_SSM': config_param.parameter_name,
                'ALPACA_DATA_FEED': data_feed,
                'OPENROUTER_MODEL': openrouter_model,
                'USE_ADVISOR': '1',
            },
            log_group=log_group,
        )
        table.grant_read_write_data(fn)
        config_param.grant_read(fn)
        # Read the SecureString secrets + decrypt them. SecureStrings encrypted
        # with the AWS-managed `aws/ssm` key need kms:Decrypt scoped via the
        # ssm service (the managed key's policy permits account IAM through SSM).
        fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=['ssm:GetParameter', 'ssm:GetParameters'],
                resources=secret_param_arns,
            )
        )
        fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=['kms:Decrypt'],
                resources=['*'],
                conditions={'StringEquals': {'kms:ViaService': f'ssm.{self.region}.amazonaws.com'}},
            )
        )

        # --- EventBridge schedules (UTC; the in-handler gate handles DST) ------
        target = targets.LambdaFunction(fn)
        # Coarse session cadence: weekdays, every 15 min across the widest ET
        # session-in-UTC window (covers both EDT 13:30-20:00 and EST 14:30-21:00).
        events.Rule(
            self,
            'SessionSchedule',
            schedule=events.Schedule.cron(minute='0/15', hour='13-21', week_day='MON-FRI'),
            targets=[target],
        )
        # MOC backstop fire at ~15:47 ET in both regimes (19:47 UTC under EDT,
        # 20:47 UTC under EST) — inside the engine's [15:45, 15:50) MOC window and
        # before Alpaca's ~15:50 submission deadline. Guarantees the resting
        # market-on-close order is placed even if the session cadence misfires.
        events.Rule(
            self,
            'MocBackstop',
            schedule=events.Schedule.cron(minute='47', hour='19,20', week_day='MON-FRI'),
            targets=[target],
        )
        # Guaranteed near-close liquidation fire at ~15:56 ET in both regimes
        # (19:56 UTC under EDT, 20:56 UTC under EST); the off-regime fire is gated
        # off as out-of-session. The strategy sells at/after force_exit_after.
        events.Rule(
            self,
            'EodLiquidation',
            schedule=events.Schedule.cron(minute='56', hour='19,20', week_day='MON-FRI'),
            targets=[target],
        )

        # --- Alarms → SNS ------------------------------------------------------
        topic = sns.Topic(self, 'AlarmTopic', display_name=f'trading-bot-{mode}-alarms')
        if alarm_email:
            topic.add_subscription(subscriptions.EmailSubscription(alarm_email))
        alarm_action = cw_actions.SnsAction(topic)

        fn.metric_errors(period=Duration.minutes(5)).create_alarm(
            self,
            'ErrorAlarm',
            threshold=1,
            evaluation_periods=1,
            alarm_description='Trading-bot Lambda raised an error.',
        ).add_alarm_action(alarm_action)

        fn.metric_duration(statistic='p99', period=Duration.minutes(5)).create_alarm(
            self,
            'DurationAlarm',
            threshold=Duration.seconds(250).to_milliseconds(),
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            alarm_description='Trading-bot run is approaching the timeout.',
        ).add_alarm_action(alarm_action)

        # --- Outputs ----------------------------------------------------------
        CfnOutput(self, 'TableName', value=table.table_name)
        CfnOutput(self, 'FunctionName', value=fn.function_name)
        CfnOutput(
            self,
            'SecretSsmParams',
            value=','.join(secret_param_names),
            description='SecureString params the Lambda reads — create these yourself.',
        )
