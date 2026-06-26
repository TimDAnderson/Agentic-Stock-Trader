"""Indicator math + the look-ahead-safe ``compute_indicators`` entry point.

All functions operate on pandas objects indexed by a tz-aware DatetimeIndex with
columns ``open, high, low, close, volume``. RSI and ATR use Wilder's smoothing.

The core safety property: ``compute_indicators`` slices the frame to bars
``<= as_of`` *before* computing anything, so an indicator can never peek at the
future. This is what makes a backtest equal to replaying historical snapshots
(DECISIONS.md §6, §11).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict

from trading_bot.domain.market_state import Indicators

ET = ZoneInfo('America/New_York')

OHLCV_COLUMNS = ('open', 'high', 'low', 'close', 'volume')


class IndicatorParams(BaseModel):
    """Bar-count windows for indicator computation.

    For minute bars these are minutes. Defaults are conventional; tuning lives
    in this object rather than scattered through the code.
    """

    model_config = ConfigDict(frozen=True)

    sma_window: int = 20
    ema_span: int = 9
    rsi_window: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    atr_window: int = 14
    rel_volume_window: int = 20


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Wilder smoothing == EMA with alpha = 1/window.
    avg_gain = gain.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss
    out = 100.0 - (100.0 / (1.0 + rs))
    # When there are no losses, RS -> inf -> RSI 100; guard the 0/0 case.
    out = out.where(avg_loss != 0.0, 100.0)
    out = out.where(~((avg_gain == 0.0) & (avg_loss == 0.0)), 50.0)
    return out


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return (macd_line, signal_line, histogram)."""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """Wilder's Average True Range."""
    high = df['high']
    low = df['low']
    prev_close = df['close'].shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()


def session_vwap(df: pd.DataFrame, tz: ZoneInfo = ET) -> pd.Series:
    """Cumulative VWAP that resets each trading session (ET calendar day)."""
    typical = (df['high'] + df['low'] + df['close']) / 3.0
    pv = typical * df['volume']
    session = df.index.tz_convert(tz).date
    grouped = pd.Series(session, index=df.index)
    cum_pv = pv.groupby(grouped).cumsum()
    cum_vol = df['volume'].groupby(grouped).cumsum()
    return cum_pv / cum_vol.replace(0.0, np.nan)


def _last(series: pd.Series) -> float | None:
    """Last non-NaN value as a float, or None if unavailable."""
    if series.empty:
        return None
    val = series.iloc[-1]
    if pd.isna(val):
        return None
    return float(val)


def _relative_volume(df: pd.DataFrame, window: int) -> float | None:
    """Latest bar volume relative to the trailing average bar volume."""
    vol = df['volume']
    if len(vol) < window + 1:
        return None
    avg = vol.iloc[-(window + 1) : -1].mean()
    if avg <= 0:
        return None
    return float(vol.iloc[-1] / avg)


def _gap_pct(df: pd.DataFrame, as_of: pd.Timestamp, tz: ZoneInfo) -> float | None:
    """Today's session open vs the prior session's close, as a fraction."""
    sessions = df.index.tz_convert(tz).date
    today = as_of.tz_convert(tz).date()
    today_mask = sessions == today
    prior_mask = sessions < today
    if not today_mask.any() or not prior_mask.any():
        return None
    today_open = float(df.loc[today_mask, 'open'].iloc[0])
    prior_close = float(df.loc[prior_mask, 'close'].iloc[-1])
    if prior_close == 0:
        return None
    return (today_open - prior_close) / prior_close


def compute_indicators(
    df: pd.DataFrame,
    symbol: str,
    as_of: datetime,
    params: IndicatorParams | None = None,
    tz: ZoneInfo = ET,
) -> Indicators:
    """Compute an ``Indicators`` snapshot for ``symbol`` as of ``as_of``.

    ``df`` must be a tz-aware OHLCV frame sorted by time. Only bars with index
    ``<= as_of`` are used — this is the look-ahead guard. Indicators that lack
    enough history come back as ``None`` (the strategy treats that as "no
    conviction").
    """
    params = params or IndicatorParams()
    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f'OHLCV frame missing columns: {missing}')
    if df.index.tz is None:
        raise ValueError('OHLCV frame index must be tz-aware (got naive DatetimeIndex).')

    as_of_ts = pd.Timestamp(as_of)
    if as_of_ts.tz is None:
        as_of_ts = as_of_ts.tz_localize(tz)

    window = df.loc[df.index <= as_of_ts]
    if window.empty:
        raise ValueError(f'No bars at or before as_of={as_of_ts} for {symbol}.')

    close = window['close']
    macd_line, signal_line, hist = macd(
        close, params.macd_fast, params.macd_slow, params.macd_signal
    )

    return Indicators(
        symbol=symbol,
        price=float(close.iloc[-1]),
        vwap=_last(session_vwap(window, tz)),
        sma=_last(sma(close, params.sma_window)),
        ema=_last(ema(close, params.ema_span)),
        rsi=_last(rsi(close, params.rsi_window)),
        macd=_last(macd_line),
        macd_signal=_last(signal_line),
        macd_hist=_last(hist),
        atr=_last(atr(window, params.atr_window)),
        relative_volume=_relative_volume(window, params.rel_volume_window),
        gap_pct=_gap_pct(window, as_of_ts, tz),
    )
