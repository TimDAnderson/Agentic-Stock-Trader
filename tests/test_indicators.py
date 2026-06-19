"""Tests for indicator math and look-ahead protection."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from tests.conftest import make_session_bars, trending_closes
from trading_bot.indicators import compute_indicators, ema, rsi, session_vwap, sma


def test_sma_matches_manual() -> None:
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    out = sma(s, 3)
    assert pd.isna(out.iloc[1])
    assert out.iloc[2] == pytest.approx(2.0)
    assert out.iloc[4] == pytest.approx(4.0)


def test_rsi_all_gains_is_100() -> None:
    s = pd.Series(trending_closes(100.0, 30, drift=1.0))
    out = rsi(s, 14)
    assert out.iloc[-1] == pytest.approx(100.0)


def test_rsi_all_losses_is_zero() -> None:
    s = pd.Series(trending_closes(100.0, 30, drift=-1.0))
    out = rsi(s, 14)
    assert out.iloc[-1] == pytest.approx(0.0)


def test_ema_insufficient_history_is_nan() -> None:
    s = pd.Series([1.0, 2.0, 3.0])
    out = ema(s, 9)
    assert out.isna().all()


def test_session_vwap_resets_each_day() -> None:
    day1 = make_session_bars(datetime(2026, 6, 1), [100.0] * 5, volume=1000.0)
    day2 = make_session_bars(datetime(2026, 6, 2), [200.0] * 5, volume=1000.0)
    df = pd.concat([day1, day2])
    vwap = session_vwap(df)
    # Day 2's first-bar vwap must reflect only day-2 prices, not day-1 carryover.
    day2_first = vwap.loc[day2.index[0]]
    assert day2_first == pytest.approx(200.0, abs=0.2)


def test_compute_indicators_is_look_ahead_safe() -> None:
    """Indicators at as_of must ignore bars after as_of."""
    closes = trending_closes(100.0, 60, drift=0.1)
    df = make_session_bars(datetime(2026, 6, 1), closes)
    as_of = df.index[30].to_pydatetime()

    full = compute_indicators(df, 'QQQ', as_of)
    truncated = compute_indicators(df.iloc[:31], 'QQQ', as_of)

    assert full.price == truncated.price
    assert full.rsi == pytest.approx(truncated.rsi)
    assert full.macd_hist == pytest.approx(truncated.macd_hist)
    # And the price is the close at as_of, not the final (future) bar.
    assert full.price == pytest.approx(closes[30])
    assert full.price != pytest.approx(closes[-1])


def test_compute_indicators_requires_tz_aware_index() -> None:
    closes = trending_closes(100.0, 30, drift=0.1)
    df = make_session_bars(datetime(2026, 6, 1), closes)
    naive = df.tz_localize(None)
    with pytest.raises(ValueError, match='tz-aware'):
        compute_indicators(naive, 'QQQ', df.index[10].to_pydatetime())


def test_relative_volume_against_average() -> None:
    closes = trending_closes(100.0, 40, drift=0.1)
    df = make_session_bars(datetime(2026, 6, 1), closes, volume=1000.0)
    # Spike the last bar's volume to 3x.
    df.iloc[-1, df.columns.get_loc('volume')] = 3000.0
    ind = compute_indicators(df, 'QQQ', df.index[-1].to_pydatetime())
    assert ind.relative_volume == pytest.approx(3.0, rel=0.01)


def test_gap_pct_uses_prior_session_close() -> None:
    day1 = make_session_bars(datetime(2026, 6, 1), [100.0, 100.0, 100.0], volume=1000.0)
    day2 = make_session_bars(datetime(2026, 6, 2), [110.0, 110.0, 110.0], volume=1000.0)
    df = pd.concat([day1, day2])
    ind = compute_indicators(df, 'QQQ', day2.index[0].to_pydatetime())
    # Day-2 open is 110 (== its first close per the builder), prior close 100 -> +10%.
    assert ind.gap_pct == pytest.approx(0.10, abs=1e-6)
