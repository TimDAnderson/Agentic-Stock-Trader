"""Local end-to-end runner (DECISIONS.md §11, §13 step 6).

Ties the whole pipeline together: gather a ``MarketState`` (LangGraph parallel
fetch) → run the ``TradingEngine`` (calendar gate → reconcile → decide → advisory
veto → conditional-write → order). ``run_once`` is dependency-injected, so the
full flow is exercised network-free in tests with fakes, and runs for real on a
laptop via ``build_local_engine`` (Alpaca paper + OpenRouter + local state).

This is the go/no-go before AWS: same code the Lambda will run, driven locally.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from typing import Any

from trading_bot.engine import TradingEngine
from trading_bot.reasoning.graph import gather_market_state
from trading_bot.reasoning.providers import MarketDataProvider
from trading_bot.state.models import RunRecord
from trading_bot.state.repository import StateRepository


def run_once(
    engine: TradingEngine,
    provider: MarketDataProvider,
    *,
    as_of: datetime | None = None,
    trade_date: date | None = None,
    recursion_limit: int = 10,
) -> RunRecord:
    """Gather a MarketState (using the broker's live equity) and run the engine once.

    ``trade_date`` overrides the day key the state machine and ``client_order_id``s
    use (defaults to ``as_of``'s ET date). Pass a synthetic date to run an extra
    buy-once/sell-once cycle on the same real day for testing — the market-open
    gate and strategy timing still use the real ``as_of``.
    """
    as_of = as_of or datetime.now(UTC)
    equity = engine.broker.get_account().equity
    state = gather_market_state(provider, as_of, equity, recursion_limit=recursion_limit)
    return engine.run(state, trade_date=trade_date)


def build_engine(
    mode: Any = None,
    *,
    use_advisor: bool = True,
    force_entry: bool = False,
    force_entry_stop_pct: float = 0.05,
    config_file: str | None = None,
    feed: str = 'iex',
    dynamodb_table: str | None = None,
    dynamodb_endpoint: str | None = None,
) -> tuple[TradingEngine, MarketDataProvider]:
    """Wire the full stack (Alpaca + OpenRouter + state) for ``mode``.

    The single seam shared by the local CLI and the AWS Lambda — "local" vs
    "deployed" is wiring, not new logic (DECISIONS.md §11). ``mode`` selects the
    Alpaca endpoint (defaults to paper); credentials come from the environment
    (the Lambda populates them from Secrets Manager first). ``StrategyConfig`` is
    resolved by ``load_strategy_config`` (``config_file`` arg, else
    ``STRATEGY_CONFIG_FILE`` / ``STRATEGY_CONFIG_SSM`` env, else defaults), so a
    local run can be made decision-equivalent to prod. ``feed`` picks the Alpaca
    data feed (``iex`` free / ``sip`` paid). State is in-memory unless a DynamoDB
    table is given (arg or ``DYNAMODB_TABLE`` env).

    ``force_entry`` swaps in ``ForceEntryStrategy`` and disables the advisor so a
    real paper order is guaranteed — pre-deploy validation only, never deployed.
    Network — not exercised by the test suite.
    """
    from trading_bot.broker import BrokerMode, load_credentials
    from trading_bot.broker.alpaca import AlpacaBroker
    from trading_bot.config_loader import load_strategy_config
    from trading_bot.data import AlpacaMarketDataProvider
    from trading_bot.market_calendar import AlpacaMarketCalendar
    from trading_bot.strategy import ForceEntryStrategy, MomentumStrategy
    from trading_bot.strategy.base import Strategy

    mode = mode or BrokerMode.PAPER
    config = load_strategy_config(file=config_file)
    creds = load_credentials(mode)
    broker = AlpacaBroker(mode, creds)  # verifies the account matches mode at startup
    calendar = AlpacaMarketCalendar(broker.trading_client)

    symbols = (config.instruments.reference_symbol, *config.instruments.tradable_symbols())
    provider = AlpacaMarketDataProvider(
        creds.api_key, creds.secret_key, symbols=tuple(set(symbols)), feed=feed
    )

    repository = _build_repository(dynamodb_table, dynamodb_endpoint)

    strategy: Strategy = (
        ForceEntryStrategy(stop_pct=force_entry_stop_pct, take_pct=force_entry_stop_pct)
        if force_entry
        else MomentumStrategy()
    )

    advisor = None
    if use_advisor and not force_entry:  # a forced entry must not be vetoed
        from trading_bot.reasoning.openrouter import OpenRouterLLM
        from trading_bot.reasoning.tot import ToTAdvisor

        model = os.environ.get('OPENROUTER_MODEL', 'openai/gpt-4o-mini')
        advisor = ToTAdvisor(OpenRouterLLM(model=model))

    engine = TradingEngine(
        broker=broker,
        repository=repository,
        strategy=strategy,
        config=config,
        advisor=advisor,
        calendar=calendar,
    )
    return engine, provider


def build_local_engine(
    *,
    use_advisor: bool = True,
    force_entry: bool = False,
    force_entry_stop_pct: float = 0.05,
    config_file: str | None = None,
    feed: str = 'iex',
    dynamodb_table: str | None = None,
    dynamodb_endpoint: str | None = None,
) -> tuple[TradingEngine, MarketDataProvider]:
    """Local convenience wrapper around :func:`build_engine` (Alpaca **paper**)."""
    from trading_bot.broker import BrokerMode

    return build_engine(
        BrokerMode.PAPER,
        use_advisor=use_advisor,
        force_entry=force_entry,
        force_entry_stop_pct=force_entry_stop_pct,
        config_file=config_file,
        feed=feed,
        dynamodb_table=dynamodb_table,
        dynamodb_endpoint=dynamodb_endpoint,
    )


def _build_repository(table: str | None, endpoint: str | None) -> StateRepository:
    """DynamoDB-backed repo when a table is configured, else in-memory."""
    table = table or os.environ.get('DYNAMODB_TABLE')
    if not table:
        from trading_bot.state import InMemoryStateRepository

        return InMemoryStateRepository()

    from trading_bot.state.dynamodb import DynamoStateRepository

    endpoint = endpoint or os.environ.get('DYNAMODB_ENDPOINT')
    # Lambda sets AWS_REGION; AWS_DEFAULT_REGION is the CLI/local convention.
    region = os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
    repo = DynamoStateRepository(
        table,
        region_name=region,
        endpoint_url=endpoint,
    )
    if endpoint:  # local DynamoDB — create the table if it isn't there yet
        repo.ensure_table()
    return repo
