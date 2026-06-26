"""Tests for StrategyConfig (de)serialization."""

from __future__ import annotations

from datetime import time

from trading_bot.domain.config import InstrumentConfig, StrategyConfig
from trading_bot.domain.decisions import EntryAction


def test_defaults() -> None:
    c = StrategyConfig()
    assert c.version == 'v1'
    assert c.no_entry_after == time(14, 30)
    assert c.force_exit_after == time(15, 55)
    assert c.target_position_usd is None  # percent-of-equity by default
    assert c.instruments == InstrumentConfig.qqq_psq()


def test_instrument_presets_and_symbol_resolution() -> None:
    qqq = InstrumentConfig.qqq_psq()
    assert qqq.symbol_for(EntryAction.BUY_BULLISH) == 'QQQ'
    assert qqq.symbol_for(EntryAction.BUY_BEARISH) == 'PSQ'
    assert qqq.symbol_for(EntryAction.DO_NOTHING) is None

    lev = InstrumentConfig.tqqq_sqqq()
    assert lev.reference_symbol == 'QQQ'  # view still read from QQQ
    assert lev.symbol_for(EntryAction.BUY_BULLISH) == 'TQQQ'
    assert lev.symbol_for(EntryAction.BUY_BEARISH) == 'SQQQ'
    assert lev.tradable_symbols() == ('TQQQ', 'SQQQ')


def test_from_dict_loads_nested_instruments() -> None:
    c = StrategyConfig.from_dict(
        {
            'target_position_usd': 500.0,
            'instruments': {
                'reference_symbol': 'QQQ',
                'bullish_symbol': 'TQQQ',
                'bearish_symbol': 'SQQQ',
            },
        }
    )
    assert c.target_position_usd == 500.0
    assert c.instruments.bullish_symbol == 'TQQQ'
    assert c.instruments.symbol_for(EntryAction.BUY_BEARISH) == 'SQQQ'


def test_from_dict_parses_times_and_ignores_unknowns() -> None:
    c = StrategyConfig.from_dict(
        {
            'version': 'v2',
            'no_entry_after': '13:00',
            'force_exit_after': '15:50',
            'max_position_pct': 0.05,
            'high_impact_levels': ['high', 'medium'],
            'some_future_field': 'ignored',
        }
    )
    assert c.version == 'v2'
    assert c.no_entry_after == time(13, 0)
    assert c.force_exit_after == time(15, 50)
    assert c.max_position_pct == 0.05
    assert c.high_impact_levels == ('high', 'medium')


def test_round_trip() -> None:
    c = StrategyConfig(version='v3', max_position_pct=0.2)
    restored = StrategyConfig.from_dict(c.to_dict())
    assert restored == c
