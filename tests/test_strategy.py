"""Tests for the deterministic MomentumStrategy (DECISIONS.md §6, §7)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from trading_bot.domain.config import InstrumentConfig, StrategyConfig
from trading_bot.domain.decisions import EntryAction, ExitAction
from trading_bot.domain.market_state import (
    EconomicEvent,
    Indicators,
    MarketState,
    Position,
)
from trading_bot.strategy import MomentumStrategy

ET = ZoneInfo('America/New_York')


def bullish_qqq(**overrides: Any) -> Indicators:
    base = Indicators(
        symbol='QQQ',
        price=100.0,
        vwap=99.0,
        ema=99.2,
        rsi=60.0,
        macd=0.3,
        macd_signal=0.1,
        macd_hist=0.2,
        atr=0.8,
        relative_volume=1.5,
        gap_pct=0.001,
    )
    return base.model_copy(update=overrides)


def bearish_qqq(**overrides: Any) -> Indicators:
    base = Indicators(
        symbol='QQQ',
        price=99.0,
        vwap=100.0,
        ema=99.8,
        rsi=40.0,
        macd=-0.3,
        macd_signal=-0.1,
        macd_hist=-0.2,
        atr=0.8,
        relative_volume=1.5,
        gap_pct=-0.001,
    )
    return base.model_copy(update=overrides)


def psq(**overrides: Any) -> Indicators:
    base = Indicators(symbol='PSQ', price=12.0, atr=0.15, vwap=12.0, macd_hist=0.0)
    return base.model_copy(update=overrides)


def state(
    indicators: dict[str, Indicators],
    *,
    hour: int = 10,
    minute: int = 0,
    equity: float = 100_000.0,
    events: tuple[EconomicEvent, ...] = (),
) -> MarketState:
    as_of = datetime(2026, 6, 1, hour, minute, tzinfo=ET)
    return MarketState(as_of=as_of, indicators=indicators, equity=equity, events=events)


def test_bullish_buys_qqq_with_bracket() -> None:
    s = state({'QQQ': bullish_qqq()})
    d = MomentumStrategy().evaluate_entry(s, StrategyConfig())
    assert d.action is EntryAction.BUY_BULLISH
    assert d.qty == 100  # 10% of 100k / $100
    assert d.stop_loss_price == 98.8  # 100 - 1.5 * 0.8
    assert d.take_profit_price == 102.4  # 100 + 3.0 * 0.8


def test_bearish_buys_psq_sized_on_psq_price() -> None:
    s = state({'QQQ': bearish_qqq(), 'PSQ': psq()})
    d = MomentumStrategy().evaluate_entry(s, StrategyConfig())
    assert d.action is EntryAction.BUY_BEARISH
    assert d.qty == 833  # floor(10000 / 12)
    assert d.stop_loss_price == 11.78  # 12 - 1.5 * 0.15


def test_target_dollar_sizing_overrides_pct() -> None:
    # ~$500 of QQQ at $100 -> 5 shares; dollar amount wins over max_position_pct.
    s = state({'QQQ': bullish_qqq()})
    d = MomentumStrategy().evaluate_entry(s, StrategyConfig(target_position_usd=500.0))
    assert d.action is EntryAction.BUY_BULLISH
    assert d.qty == 5


def test_leveraged_preset_trades_tqqq_sized_on_its_own_price() -> None:
    # Bull view read from QQQ, but the trade (and sizing/stop) is on TQQQ.
    tqqq = Indicators(
        symbol='TQQQ',
        price=80.0,
        vwap=79.0,
        ema=79.0,
        rsi=60.0,
        macd=0.3,
        macd_signal=0.1,
        macd_hist=0.2,
        atr=1.5,
        relative_volume=1.5,
        gap_pct=0.0,
    )
    s = state({'QQQ': bullish_qqq(), 'TQQQ': tqqq})
    cfg = StrategyConfig(instruments=InstrumentConfig.tqqq_sqqq(), target_position_usd=800.0)
    d = MomentumStrategy().evaluate_entry(s, cfg)
    assert d.action is EntryAction.BUY_BULLISH
    assert d.qty == 10  # floor(800 / 80), sized on TQQQ's price
    assert d.stop_loss_price == 77.75  # 80 - 1.5 * 1.5 ATR


def test_mixed_signals_do_nothing() -> None:
    # price above vwap but macd negative -> not aligned.
    s = state({'QQQ': bullish_qqq(macd_hist=-0.1)})
    d = MomentumStrategy().evaluate_entry(s, StrategyConfig())
    assert d.action is EntryAction.DO_NOTHING


def test_entry_cutoff_blocks_late_entry() -> None:
    s = state({'QQQ': bullish_qqq()}, hour=15, minute=0)
    d = MomentumStrategy().evaluate_entry(s, StrategyConfig())
    assert d.action is EntryAction.DO_NOTHING
    assert 'cutoff' in d.reason.lower()


def test_low_relative_volume_blocks_entry() -> None:
    s = state({'QQQ': bullish_qqq(relative_volume=0.5)})
    d = MomentumStrategy().evaluate_entry(s, StrategyConfig(min_relative_volume=1.0))
    assert d.action is EntryAction.DO_NOTHING
    assert 'volume' in d.reason.lower()


def test_high_impact_event_blocks_entry() -> None:
    ev = EconomicEvent(timestamp=datetime(2026, 6, 1, 10, 30, tzinfo=ET), name='CPI', impact='high')
    s = state({'QQQ': bullish_qqq()}, hour=10, minute=0, events=(ev,))
    d = MomentumStrategy().evaluate_entry(s, StrategyConfig())
    assert d.action is EntryAction.DO_NOTHING
    assert 'CPI' in d.reason


def test_quiet_market_blocks_entry() -> None:
    s = state({'QQQ': bullish_qqq(atr=0.001)})
    d = MomentumStrategy().evaluate_entry(s, StrategyConfig(min_atr_pct=0.001))
    assert d.action is EntryAction.DO_NOTHING


def test_insufficient_history_do_nothing() -> None:
    s = state({'QQQ': bullish_qqq(rsi=None)})
    d = MomentumStrategy().evaluate_entry(s, StrategyConfig())
    assert d.action is EntryAction.DO_NOTHING
    assert 'history' in d.reason.lower()


def test_exit_forced_near_close() -> None:
    pos = Position(symbol='QQQ', qty=100, avg_entry_price=100.0, current_price=101.0)
    s = state({'QQQ': bullish_qqq(price=101.0)}, hour=15, minute=56)
    d = MomentumStrategy().evaluate_exit(s, pos, StrategyConfig())
    assert d.action is ExitAction.SELL


def test_exit_holds_winner() -> None:
    pos = Position(symbol='QQQ', qty=100, avg_entry_price=100.0, current_price=101.5)
    s = state({'QQQ': bullish_qqq(price=101.5)}, hour=11, minute=0)
    d = MomentumStrategy().evaluate_exit(s, pos, StrategyConfig())
    assert d.action is ExitAction.HOLD


def test_exit_on_momentum_rollover() -> None:
    pos = Position(symbol='QQQ', qty=100, avg_entry_price=100.0, current_price=98.5)
    rolled = bullish_qqq(price=98.5, vwap=99.5, macd_hist=-0.3)
    s = state({'QQQ': rolled}, hour=11, minute=0)
    d = MomentumStrategy().evaluate_exit(s, pos, StrategyConfig())
    assert d.action is ExitAction.SELL
