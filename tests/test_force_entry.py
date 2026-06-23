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
    assert d.stop_loss_price is not None and d.stop_loss_price < 400.0


def test_force_entry_works_without_atr() -> None:
    # Early-session: no ATR yet — still buys, using a fallback percentage stop.
    s = _state({'QQQ': Indicators(symbol='QQQ', price=400.0)})
    d = ForceEntryStrategy().evaluate_entry(s, StrategyConfig())
    assert d.action is EntryAction.BUY_BULLISH
    assert d.stop_loss_price == 394.0  # 400 * (1 - 0.01) via fallback ATR


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
