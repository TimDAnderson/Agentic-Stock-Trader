"""Tests for the dev-only ForceEntryStrategy."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from trading_bot.domain.config import InstrumentConfig, StrategyConfig
from trading_bot.domain.decisions import EntryAction, ExitAction
from trading_bot.domain.market_state import Indicators, MarketState, Position
from trading_bot.strategy import ForceEntryStrategy

ET = ZoneInfo('America/New_York')


def _state(indicators: dict[str, Indicators], *, hour: int = 10) -> MarketState:
    return MarketState(
        as_of=datetime(2026, 6, 2, hour, 0, tzinfo=ET), indicators=indicators, equity=100_000.0
    )


def test_force_entry_always_buys_and_sizes() -> None:
    s = _state({'QQQ': Indicators(symbol='QQQ', price=400.0, atr=0.8)})
    d = ForceEntryStrategy().evaluate_entry(s, StrategyConfig())
    assert d.action is EntryAction.BUY_BULLISH
    assert d.qty == 25  # floor(10% of 100k / $400)
    # Wide fixed-% bracket (default 5%), not the tight ATR stop.
    assert d.stop_loss_price == 380.0  # 400 * (1 - 0.05)
    assert d.take_profit_price == 420.0  # 400 * (1 + 0.05)


def test_force_entry_stop_ignores_tiny_atr() -> None:
    # A tiny minute-bar ATR must NOT produce a near-entry stop that fills instantly.
    s = _state({'QQQ': Indicators(symbol='QQQ', price=400.0, atr=0.3)})
    d = ForceEntryStrategy().evaluate_entry(s, StrategyConfig())
    assert d.stop_loss_price == 380.0  # still the wide 5% stop, unaffected by ATR


def test_force_entry_stop_pct_is_configurable() -> None:
    s = _state({'QQQ': Indicators(symbol='QQQ', price=400.0)})
    d = ForceEntryStrategy(stop_pct=0.005, take_pct=0.02).evaluate_entry(s, StrategyConfig())
    assert d.stop_loss_price == 398.0  # 400 * (1 - 0.005)
    assert d.take_profit_price == 408.0  # 400 * (1 + 0.02)


def test_force_entry_respects_target_dollars_and_min_one_share() -> None:
    s = _state({'QQQ': Indicators(symbol='QQQ', price=400.0, atr=0.8)})
    d = ForceEntryStrategy().evaluate_entry(s, StrategyConfig(target_position_usd=100.0))
    assert d.qty == 1  # floor(100 / 400) == 0 -> clamped to at least one share


def test_force_entry_uses_configured_bullish_symbol() -> None:
    s = _state({'TQQQ': Indicators(symbol='TQQQ', price=80.0, atr=1.5)})
    cfg = StrategyConfig(instruments=InstrumentConfig.tqqq_sqqq(), target_position_usd=800.0)
    d = ForceEntryStrategy().evaluate_entry(s, cfg)
    assert d.action is EntryAction.BUY_BULLISH
    assert d.qty == 10  # floor(800 / 80), sized on TQQQ


def test_force_entry_delegates_exit() -> None:
    pos = Position(symbol='QQQ', qty=25, avg_entry_price=400.0, current_price=401.0)
    # Past forced-exit time -> the delegated MomentumStrategy says SELL.
    s = _state({'QQQ': Indicators(symbol='QQQ', price=401.0)}, hour=15)
    s = s.model_copy(update={'as_of': datetime(2026, 6, 2, 15, 56, tzinfo=ET)})
    d = ForceEntryStrategy().evaluate_exit(s, pos, StrategyConfig())
    assert d.action is ExitAction.SELL
