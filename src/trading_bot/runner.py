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
from datetime import UTC, datetime

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
    recursion_limit: int = 10,
) -> RunRecord:
    """Gather a MarketState (using the broker's live equity) and run the engine once."""
    as_of = as_of or datetime.now(UTC)
    equity = engine.broker.get_account().equity
    state = gather_market_state(provider, as_of, equity, recursion_limit=recursion_limit)
    return engine.run(state)


def build_local_engine(
    *,
    use_advisor: bool = True,
    force_entry: bool = False,
    dynamodb_table: str | None = None,
    dynamodb_endpoint: str | None = None,
) -> tuple[TradingEngine, MarketDataProvider]:
    """Wire the real local stack from env (Alpaca paper + OpenRouter).

    Requires the ``broker`` and ``reasoning`` extras and ``ALPACA_*`` /
    ``OPENROUTER_*`` env vars. State is in-memory unless a DynamoDB table is
    given (arg or ``DYNAMODB_TABLE`` env) — point ``DYNAMODB_ENDPOINT`` at
    DynamoDB Local (and add the ``aws`` extra) to use the real persistence path.

    ``force_entry`` swaps in ``ForceEntryStrategy`` and disables the advisor so a
    real paper order is guaranteed (the market-calendar gate still applies) — for
    pre-deploy validation only. Network — not exercised by the test suite.
    """
    from trading_bot.broker import BrokerMode, load_credentials
    from trading_bot.broker.alpaca import AlpacaBroker
    from trading_bot.data import AlpacaMarketDataProvider
    from trading_bot.domain.config import StrategyConfig
    from trading_bot.market_calendar import AlpacaMarketCalendar
    from trading_bot.strategy import ForceEntryStrategy, MomentumStrategy
    from trading_bot.strategy.base import Strategy

    config = StrategyConfig()
    creds = load_credentials(BrokerMode.PAPER)
    broker = AlpacaBroker(BrokerMode.PAPER, creds)  # verifies paper account at startup
    calendar = AlpacaMarketCalendar(broker.trading_client)

    symbols = (config.instruments.reference_symbol, *config.instruments.tradable_symbols())
    provider = AlpacaMarketDataProvider(
        creds.api_key, creds.secret_key, symbols=tuple(set(symbols))
    )

    repository = _build_repository(dynamodb_table, dynamodb_endpoint)

    strategy: Strategy = ForceEntryStrategy() if force_entry else MomentumStrategy()

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


def _build_repository(table: str | None, endpoint: str | None) -> StateRepository:
    """DynamoDB-backed repo when a table is configured, else in-memory."""
    table = table or os.environ.get('DYNAMODB_TABLE')
    if not table:
        from trading_bot.state import InMemoryStateRepository

        return InMemoryStateRepository()

    from trading_bot.state.dynamodb import DynamoStateRepository

    endpoint = endpoint or os.environ.get('DYNAMODB_ENDPOINT')
    repo = DynamoStateRepository(
        table,
        region_name=os.environ.get('AWS_DEFAULT_REGION', 'us-east-1'),
        endpoint_url=endpoint,
    )
    if endpoint:  # local DynamoDB — create the table if it isn't there yet
        repo.ensure_table()
    return repo
