"""DynamoDB state repository (DECISIONS.md §4, §9).

Single-table design with ``PK="DATE#<date>"`` and an ``SK`` of ``STATE`` /
``RUN#<ts>`` / ``TRADE#<order_id>``. Records are stored as a JSON blob in a
``data`` attribute (sidesteps the float→Decimal friction); the STATE row also
keeps ``status`` as a first-class attribute so transitions can be **conditional
writes** gated on it. A GSI (``GSI1``) keyed on ``VER#<strategy_version>`` backs
cross-day queries by strategy version.

boto3 is an optional dependency (``uv sync --extra aws``), imported lazily so the
rest of the package works without it. The conditional-write semantics here are
the real double-buy gate; the in-memory repo mirrors them for tests.
"""

from __future__ import annotations

from datetime import date
from typing import Any, NamedTuple

from trading_bot.state.errors import ConcurrentTransitionError, StateAlreadyExistsError
from trading_bot.state.models import DailyState, PositionStatus, RunRecord, TradeRecord

_GSI_NAME = 'GSI1'


class _Boto3(NamedTuple):
    resource: Any
    Key: Any
    Attr: Any
    ClientError: Any


def _load_boto3() -> _Boto3:
    try:
        import boto3
        from boto3.dynamodb.conditions import Attr, Key
        from botocore.exceptions import ClientError
    except ImportError as exc:  # pragma: no cover - only without the aws extra
        from trading_bot.broker.errors import BrokerNotConfiguredError

        raise BrokerNotConfiguredError(
            'boto3 is not installed. Install the aws extra: `uv sync --extra aws`.'
        ) from exc
    return _Boto3(resource=boto3.resource, Key=Key, Attr=Attr, ClientError=ClientError)


def _pk(trade_date: date) -> str:
    return f'DATE#{trade_date.isoformat()}'


def _is_conditional_failure(error: Any, client_error_cls: Any) -> bool:
    return (
        isinstance(error, client_error_cls)
        and error.response.get('Error', {}).get('Code') == 'ConditionalCheckFailedException'
    )


class DynamoStateRepository:
    """State repository backed by a single DynamoDB table."""

    def __init__(
        self,
        table_name: str,
        *,
        region_name: str | None = None,
        endpoint_url: str | None = None,
        table: Any | None = None,
    ) -> None:
        self._sdk = _load_boto3()
        self._region_name = region_name
        self._endpoint_url = endpoint_url
        if table is not None:
            self._table = table
        else:
            self._table = self._make_resource().Table(table_name)
        self._table_name = table_name

    def _make_resource(self) -> Any:
        return self._sdk.resource(
            'dynamodb', region_name=self._region_name, endpoint_url=self._endpoint_url
        )

    # -- StateRepository ----------------------------------------------------
    def get_daily_state(self, trade_date: date) -> DailyState | None:
        resp = self._table.get_item(Key={'PK': _pk(trade_date), 'SK': 'STATE'})
        item = resp.get('Item')
        return DailyState.model_validate_json(item['data']) if item else None

    def create_daily_state(self, state: DailyState) -> DailyState:
        try:
            self._table.put_item(
                Item={
                    'PK': _pk(state.trade_date),
                    'SK': 'STATE',
                    'status': state.status.value,
                    'data': state.model_dump_json(),
                },
                ConditionExpression=self._sdk.Attr('SK').not_exists(),
            )
        except self._sdk.ClientError as exc:
            if _is_conditional_failure(exc, self._sdk.ClientError):
                raise StateAlreadyExistsError(
                    f'Daily state for {state.trade_date} already exists.'
                ) from exc
            raise
        return state

    def transition_status(self, expected: PositionStatus, updated: DailyState) -> DailyState:
        try:
            self._table.put_item(
                Item={
                    'PK': _pk(updated.trade_date),
                    'SK': 'STATE',
                    'status': updated.status.value,
                    'data': updated.model_dump_json(),
                },
                ConditionExpression=self._sdk.Attr('status').eq(expected.value),
            )
        except self._sdk.ClientError as exc:
            if _is_conditional_failure(exc, self._sdk.ClientError):
                raise ConcurrentTransitionError(
                    f'Expected status {expected} for {updated.trade_date}; guard failed.'
                ) from exc
            raise
        return updated

    def append_run(self, run: RunRecord) -> None:
        self._table.put_item(
            Item={
                'PK': _pk(run.trade_date),
                'SK': f'RUN#{run.ts.isoformat()}',
                'data': run.model_dump_json(),
            }
        )

    def append_trade(self, trade: TradeRecord) -> None:
        self._table.put_item(
            Item={
                'PK': _pk(trade.trade_date),
                'SK': f'TRADE#{trade.order_id}',
                'GSI1PK': f'VER#{trade.strategy_version}',
                'GSI1SK': f'{trade.trade_date.isoformat()}#{trade.order_id}',
                'data': trade.model_dump_json(),
            }
        )

    def list_runs(self, trade_date: date) -> list[RunRecord]:
        items = self._query(
            self._sdk.Key('PK').eq(_pk(trade_date)) & self._sdk.Key('SK').begins_with('RUN#')
        )
        return [RunRecord.model_validate_json(i['data']) for i in items]

    def list_trades(self, trade_date: date) -> list[TradeRecord]:
        items = self._query(
            self._sdk.Key('PK').eq(_pk(trade_date)) & self._sdk.Key('SK').begins_with('TRADE#')
        )
        return [TradeRecord.model_validate_json(i['data']) for i in items]

    def list_trades_by_version(self, strategy_version: str) -> list[TradeRecord]:
        items = self._query(
            self._sdk.Key('GSI1PK').eq(f'VER#{strategy_version}'), index_name=_GSI_NAME
        )
        return [TradeRecord.model_validate_json(i['data']) for i in items]

    # -- helpers ------------------------------------------------------------
    def _query(self, condition: Any, *, index_name: str | None = None) -> list[Any]:
        kwargs: dict[str, Any] = {'KeyConditionExpression': condition}
        if index_name is not None:
            kwargs['IndexName'] = index_name
        items: list[Any] = []
        while True:
            resp = self._table.query(**kwargs)
            items.extend(resp.get('Items', []))
            start_key = resp.get('LastEvaluatedKey')
            if not start_key:
                return items
            kwargs['ExclusiveStartKey'] = start_key

    def ensure_table(self) -> None:
        """Create the table + GSI if absent (for DynamoDB Local / dev).

        Production tables are provisioned by CDK (phase 6); this is a convenience
        for local integration testing against DynamoDB Local.
        """
        resource = self._make_resource()
        existing = {t.name for t in resource.tables.all()}
        if self._table_name in existing:
            return
        table = resource.create_table(
            TableName=self._table_name,
            BillingMode='PAY_PER_REQUEST',
            AttributeDefinitions=[
                {'AttributeName': 'PK', 'AttributeType': 'S'},
                {'AttributeName': 'SK', 'AttributeType': 'S'},
                {'AttributeName': 'GSI1PK', 'AttributeType': 'S'},
                {'AttributeName': 'GSI1SK', 'AttributeType': 'S'},
            ],
            KeySchema=[
                {'AttributeName': 'PK', 'KeyType': 'HASH'},
                {'AttributeName': 'SK', 'KeyType': 'RANGE'},
            ],
            GlobalSecondaryIndexes=[
                {
                    'IndexName': _GSI_NAME,
                    'KeySchema': [
                        {'AttributeName': 'GSI1PK', 'KeyType': 'HASH'},
                        {'AttributeName': 'GSI1SK', 'KeyType': 'RANGE'},
                    ],
                    'Projection': {'ProjectionType': 'ALL'},
                }
            ],
        )
        table.wait_until_exists()
