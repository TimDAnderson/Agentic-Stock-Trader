"""Tests for the backtest harness mechanics.

Harness behavior is tested with a tiny deterministic stub strategy so the
mechanics (buy-once, bracket stop, EOD close, compounding) are isolated from
MomentumStrategy tuning. A final smoke test runs the real strategy end-to-end.
"""

from __future__ import annotations

from datetime import datetime, time

import pandas as pd

from tests.conftest import make_session_bars, trending_closes
from trading_bot.backtest import Backtester
from trading_bot.domain.config import StrategyConfig
from trading_bot.domain.decisions import EntryAction, EntryDecision, ExitDecision
from trading_bot.domain.market_state import MarketState, Position
from trading_bot.strategy import MomentumStrategy


class BuyOnceStub:
    """Buys a fixed QQQ lot on the first pre-cutoff run; never sells voluntarily."""

    version = 'stub-1'

    def __init__(self, qty: int = 10, stop_offset: float = 1.0, take_offset: float = 50.0) -> None:
        self.qty = qty
        self.stop_offset = stop_offset
        self.take_offset = take_offset

    def evaluate_entry(self, state: MarketState, config: StrategyConfig) -> EntryDecision:
        ind = state.indicators_for('QQQ')
        if ind is None or state.as_of.timetz().replace(tzinfo=None) > time(14, 30):
            return EntryDecision.do_nothing('stub: no entry')
        return EntryDecision(
            action=EntryAction.BUY_BULLISH,
            qty=self.qty,
            stop_loss_price=round(ind.price - self.stop_offset, 2),
            take_profit_price=round(ind.price + self.take_offset, 2),
            reason='stub buy',
        )

    def evaluate_exit(
        self, state: MarketState, position: Position, config: StrategyConfig
    ) -> ExitDecision:
        return ExitDecision.hold('stub holds to EOD/stop')


def _data(
    closes_qqq: list[float], closes_psq: list[float] | None = None
) -> dict[str, pd.DataFrame]:
    day = datetime(2026, 6, 1)
    qqq = make_session_bars(day, closes_qqq, volume=1000.0)
    psq = make_session_bars(day, closes_psq or [12.0] * len(closes_qqq), volume=1000.0)
    return {'QQQ': qqq, 'PSQ': psq}


def test_buy_once_then_eod_close_profit() -> None:
    closes = trending_closes(100.0, 12, drift=0.5)  # rises to 105.5
    bt = Backtester(_data(closes), BuyOnceStub(qty=10), StrategyConfig(), start_equity=100_000.0)
    result = bt.run()

    assert result.trading_days == 1
    assert result.num_trades == 1  # bought once despite many runs
    trade = result.trades[0]
    assert trade.exit_kind == 'eod'
    assert trade.entry_price == 100.0
    assert trade.exit_price == closes[-1]
    assert trade.pnl > 0
    assert result.end_equity > result.start_equity


def test_stop_loss_fills_at_stop() -> None:
    closes = trending_closes(100.0, 12, drift=-0.5)  # falls through 100 - 1.0 stop
    bt = Backtester(_data(closes), BuyOnceStub(qty=10, stop_offset=1.0), StrategyConfig())
    result = bt.run()

    assert result.num_trades == 1
    trade = result.trades[0]
    assert trade.exit_kind == 'stop'
    assert trade.exit_price == 99.0  # entry 100 - 1.0
    assert trade.pnl < 0


def test_do_nothing_day_records_no_trade() -> None:
    closes = [100.0] * 12
    # Stub only buys before cutoff; force cutoff past by starting at 15:00.
    day = datetime(2026, 6, 1)
    qqq = make_session_bars(day, closes, start=time(15, 0))
    psq = make_session_bars(day, [12.0] * 12, start=time(15, 0))
    bt = Backtester({'QQQ': qqq, 'PSQ': psq}, BuyOnceStub(), StrategyConfig())
    result = bt.run()

    assert result.num_trades == 0
    assert result.do_nothing_days == 1
    assert result.end_equity == result.start_equity


def test_multi_day_compounding_and_summary() -> None:
    d1 = make_session_bars(datetime(2026, 6, 1), trending_closes(100.0, 12, 0.5))
    d2 = make_session_bars(datetime(2026, 6, 2), trending_closes(100.0, 12, 0.5))
    p1 = make_session_bars(datetime(2026, 6, 1), [12.0] * 12)
    p2 = make_session_bars(datetime(2026, 6, 2), [12.0] * 12)
    data = {'QQQ': pd.concat([d1, d2]), 'PSQ': pd.concat([p1, p2])}
    bt = Backtester(data, BuyOnceStub(qty=10), StrategyConfig())
    result = bt.run()

    assert result.trading_days == 2
    assert result.num_trades == 2
    assert len(result.equity_curve) == 2
    assert isinstance(result.summary(), str)


def test_real_strategy_runs_end_to_end() -> None:
    closes = trending_closes(100.0, 60, drift=0.05)
    bt = Backtester(_data(closes), MomentumStrategy(), StrategyConfig(), decision_step=5)
    result = bt.run()
    # We don't assert a particular action — just that the real strategy drives
    # the harness without error and yields a coherent result.
    assert result.trading_days == 1
    assert result.num_trades >= 0
    assert result.start_equity == 100_000.0
