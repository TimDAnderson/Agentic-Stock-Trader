"""Tests for the TradingEngine state machine (DECISIONS.md §4).

Uses a deterministic stub strategy with FakeBroker + InMemoryStateRepository so
the reconcile/route/decide/conditional-write flow is exercised with no network.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from trading_bot.broker import BrokerMode, FakeBroker
from trading_bot.domain.config import StrategyConfig
from trading_bot.domain.decisions import EntryAction, EntryDecision, ExitDecision
from trading_bot.domain.market_state import Indicators, MarketState, Position
from trading_bot.engine import TradingEngine
from trading_bot.market_calendar import StaticMarketCalendar
from trading_bot.reasoning import FakeAdvisor, Recommendation
from trading_bot.state import InMemoryStateRepository, PositionStatus

ET = ZoneInfo('America/New_York')
AS_OF = datetime(2026, 6, 2, 10, 0, tzinfo=ET)


class StubStrategy:
    """Returns fixed entry/exit decisions so the engine flow is deterministic."""

    version = 'stub-1'

    def __init__(self, entry: EntryDecision, exit_decision: ExitDecision) -> None:
        self._entry = entry
        self._exit = exit_decision

    def evaluate_entry(self, state: MarketState, config: StrategyConfig) -> EntryDecision:
        return self._entry

    def evaluate_exit(
        self, state: MarketState, position: Position, config: StrategyConfig
    ) -> ExitDecision:
        return self._exit


class FakeClock:
    """Monotonic clock so run/trade timestamps are distinct and deterministic."""

    def __init__(self) -> None:
        self._t = datetime(2026, 6, 2, 14, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self._t += timedelta(seconds=1)
        return self._t


def _state() -> MarketState:
    qqq = Indicators(symbol='QQQ', price=400.0, rsi=60.0, macd_hist=0.2, vwap=399.0)
    return MarketState(as_of=AS_OF, indicators={'QQQ': qqq}, equity=100_000.0)


def _buy() -> EntryDecision:
    return EntryDecision(
        action=EntryAction.BUY_BULLISH,
        qty=10,
        stop_loss_price=395.0,
        take_profit_price=410.0,
        reason='stub buy',
    )


def _engine(entry: EntryDecision, exit_decision: ExitDecision, broker: FakeBroker) -> TradingEngine:
    return TradingEngine(
        broker=broker,
        repository=InMemoryStateRepository(),
        strategy=StubStrategy(entry, exit_decision),
        config=StrategyConfig(),
        clock=FakeClock(),
    )


def test_do_nothing_entry_stays_flat() -> None:
    broker = FakeBroker(BrokerMode.PAPER, prices={'QQQ': 400.0})
    engine = _engine(EntryDecision.do_nothing('no conviction'), ExitDecision.hold('x'), broker)
    run = engine.run(_state())
    assert run.action == EntryAction.DO_NOTHING.value
    assert run.status_after is PositionStatus.NO_POSITION
    assert broker.get_positions() == []
    assert engine.repository.list_trades(run.trade_date) == []


def test_entry_opens_position_and_records() -> None:
    broker = FakeBroker(BrokerMode.PAPER, prices={'QQQ': 400.0})
    engine = _engine(_buy(), ExitDecision.hold('hold'), broker)
    run = engine.run(_state())

    assert run.action == EntryAction.BUY_BULLISH.value
    assert run.status_after is PositionStatus.POSITION_OPEN

    state = engine.repository.get_daily_state(run.trade_date)
    assert state is not None and state.status is PositionStatus.POSITION_OPEN
    assert state.entry_order_id == '2026-06-02-ENTRY'

    pos = broker.get_position('QQQ')
    assert pos is not None and pos.qty == 10

    trades = engine.repository.list_trades(run.trade_date)
    assert len(trades) == 1 and trades[0].kind == 'entry'
    assert trades[0].order_id == '2026-06-02-ENTRY'  # idempotency key


def test_second_run_while_open_routes_to_exit_not_reentry() -> None:
    broker = FakeBroker(BrokerMode.PAPER, prices={'QQQ': 400.0})
    engine = _engine(_buy(), ExitDecision.sell('take profit'), broker)

    engine.run(_state())  # entry
    broker.set_price('QQQ', 405.0)
    run2 = engine.run(_state())  # now OPEN -> exit

    assert run2.action == 'SELL'
    assert run2.status_after is PositionStatus.POSITION_CLOSED
    assert broker.get_positions() == []

    trades = engine.repository.list_trades(run2.trade_date)
    kinds = sorted(t.kind for t in trades)
    assert kinds == ['entry', 'exit']
    exit_trade = next(t for t in trades if t.kind == 'exit')
    assert exit_trade.pnl == 50.0  # (405 - 400) * 10


def test_hold_keeps_position_open() -> None:
    broker = FakeBroker(BrokerMode.PAPER, prices={'QQQ': 400.0})
    engine = _engine(_buy(), ExitDecision.hold('not yet'), broker)
    engine.run(_state())  # entry
    run2 = engine.run(_state())  # OPEN -> hold
    assert run2.action == 'HOLD'
    assert run2.status_after is PositionStatus.POSITION_OPEN
    assert broker.get_position('QQQ') is not None


def test_reconcile_marks_closed_when_broker_flat() -> None:
    broker = FakeBroker(BrokerMode.PAPER, prices={'QQQ': 400.0})
    engine = _engine(_buy(), ExitDecision.hold('hold'), broker)
    engine.run(_state())  # entry -> OPEN

    # Simulate the bracket stop firing out-of-band: broker goes flat.
    broker.close_all_positions()

    run = engine.run(_state())  # DB OPEN but broker flat -> reconcile to CLOSED
    assert run.action == 'RECONCILE'
    assert run.status_after is PositionStatus.POSITION_CLOSED
    assert run.alert is not None
    state = engine.repository.get_daily_state(run.trade_date)
    assert state is not None and state.status is PositionStatus.POSITION_CLOSED


def test_advisor_veto_blocks_a_buy() -> None:
    broker = FakeBroker(BrokerMode.PAPER, prices={'QQQ': 400.0})
    engine = TradingEngine(
        broker=broker,
        repository=InMemoryStateRepository(),
        strategy=StubStrategy(_buy(), ExitDecision.hold('x')),
        config=StrategyConfig(),
        advisor=FakeAdvisor(Recommendation.VETO, 'event risk'),
        clock=FakeClock(),
    )
    run = engine.run(_state())
    assert run.action == EntryAction.DO_NOTHING.value
    assert run.status_after is PositionStatus.NO_POSITION
    assert broker.get_positions() == []  # the veto prevented the order
    assert run.advisory is not None and run.advisory['recommendation'] == 'VETO'
    assert 'VETO' in run.reason


def test_advisor_proceed_allows_a_buy() -> None:
    broker = FakeBroker(BrokerMode.PAPER, prices={'QQQ': 400.0})
    engine = TradingEngine(
        broker=broker,
        repository=InMemoryStateRepository(),
        strategy=StubStrategy(_buy(), ExitDecision.hold('x')),
        config=StrategyConfig(),
        advisor=FakeAdvisor(Recommendation.PROCEED),
        clock=FakeClock(),
    )
    run = engine.run(_state())
    assert run.action == EntryAction.BUY_BULLISH.value
    assert run.status_after is PositionStatus.POSITION_OPEN
    assert run.advisory is not None and run.advisory['recommendation'] == 'PROCEED'


def test_market_closed_gate_does_nothing() -> None:
    broker = FakeBroker(BrokerMode.PAPER, prices={'QQQ': 400.0})
    repo = InMemoryStateRepository()
    engine = TradingEngine(
        broker=broker,
        repository=repo,
        strategy=StubStrategy(_buy(), ExitDecision.hold('x')),
        config=StrategyConfig(),
        calendar=StaticMarketCalendar(),  # no sessions -> always closed
        clock=FakeClock(),
    )
    run = engine.run(_state())
    assert run.action == 'DO_NOTHING'
    assert 'closed' in run.reason.lower()
    assert broker.get_positions() == []
    assert repo.get_daily_state(run.trade_date) is None  # no state row created when closed


def test_market_open_gate_allows_run() -> None:
    broker = FakeBroker(BrokerMode.PAPER, prices={'QQQ': 400.0})
    engine = TradingEngine(
        broker=broker,
        repository=InMemoryStateRepository(),
        strategy=StubStrategy(_buy(), ExitDecision.hold('x')),
        config=StrategyConfig(),
        calendar=StaticMarketCalendar.regular([date(2026, 6, 2)]),
        clock=FakeClock(),
    )
    run = engine.run(_state())  # as_of 2026-06-02 10:00 ET — inside the session
    assert run.action == EntryAction.BUY_BULLISH.value
    assert run.status_after is PositionStatus.POSITION_OPEN


def test_closed_day_is_terminal() -> None:
    broker = FakeBroker(BrokerMode.PAPER, prices={'QQQ': 400.0})
    engine = _engine(_buy(), ExitDecision.sell('exit'), broker)
    engine.run(_state())  # entry
    engine.run(_state())  # exit -> CLOSED
    run3 = engine.run(_state())  # terminal
    assert run3.action == 'DO_NOTHING'
    assert run3.status_after is PositionStatus.POSITION_CLOSED
