"""Full-pipeline test of run_once: gather graph -> engine, all fakes, no network.

This is the network-free proof that the whole flow works end-to-end — the same
``run_once`` the laptop CLI calls, just with injected fakes.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from trading_bot.broker import BrokerMode, FakeBroker
from trading_bot.domain.config import StrategyConfig
from trading_bot.domain.market_state import Indicators
from trading_bot.engine import TradingEngine
from trading_bot.market_calendar import StaticMarketCalendar
from trading_bot.reasoning import FakeAdvisor, Recommendation
from trading_bot.reasoning.providers import StaticMarketDataProvider
from trading_bot.runner import run_once
from trading_bot.state import InMemoryStateRepository, PositionStatus
from trading_bot.strategy import MomentumStrategy

ET = ZoneInfo('America/New_York')
AS_OF = datetime(2026, 6, 2, 10, 0, tzinfo=ET)


def _bullish_provider() -> StaticMarketDataProvider:
    qqq = Indicators(
        symbol='QQQ',
        price=400.0,
        vwap=399.0,
        ema=399.2,
        rsi=60.0,
        macd=0.3,
        macd_signal=0.1,
        macd_hist=0.2,
        atr=0.8,
        relative_volume=1.5,
        gap_pct=0.001,
    )
    return StaticMarketDataProvider({'QQQ': qqq})


def _engine(broker: FakeBroker, **kwargs: object) -> TradingEngine:
    return TradingEngine(
        broker=broker,
        repository=InMemoryStateRepository(),
        strategy=MomentumStrategy(),
        config=StrategyConfig(),
        calendar=StaticMarketCalendar.regular([date(2026, 6, 2)]),
        **kwargs,  # type: ignore[arg-type]
    )


def test_run_once_drives_full_flow_to_an_entry() -> None:
    broker = FakeBroker(BrokerMode.PAPER, prices={'QQQ': 400.0})
    engine = _engine(broker)
    run = run_once(engine, _bullish_provider(), as_of=AS_OF)

    assert run.action == 'BUY_BULLISH'
    assert run.status_after is PositionStatus.POSITION_OPEN
    pos = broker.get_position('QQQ')
    assert pos is not None and pos.qty == 25  # floor(10% of 100k / $400)


def test_run_once_respects_the_calendar_gate() -> None:
    broker = FakeBroker(BrokerMode.PAPER, prices={'QQQ': 400.0})
    engine = TradingEngine(
        broker=broker,
        repository=InMemoryStateRepository(),
        strategy=MomentumStrategy(),
        config=StrategyConfig(),
        calendar=StaticMarketCalendar(),  # closed
    )
    run = run_once(engine, _bullish_provider(), as_of=AS_OF)
    assert run.action == 'DO_NOTHING'
    assert broker.get_positions() == []


def test_run_once_advisory_veto_blocks_entry() -> None:
    broker = FakeBroker(BrokerMode.PAPER, prices={'QQQ': 400.0})
    engine = _engine(broker, advisor=FakeAdvisor(Recommendation.VETO, 'too risky'))
    run = run_once(engine, _bullish_provider(), as_of=AS_OF)
    assert run.action == 'DO_NOTHING'
    assert broker.get_positions() == []
    assert run.advisory is not None and run.advisory['recommendation'] == 'VETO'
