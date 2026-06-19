"""Code-computed signals from raw OHLCV (DECISIONS.md §8).

Code computes signals; the LLM only interprets them. Never ask a model to do
arithmetic on raw numbers — compute RSI/VWAP/MACD/ATR/relative-volume/gap here
and hand the strategy clean values.

The single entry point, ``compute_indicators``, enforces look-ahead protection:
it only ever reads bars at or before ``as_of``.
"""

from trading_bot.indicators.compute import (
    IndicatorParams,
    atr,
    compute_indicators,
    ema,
    macd,
    rsi,
    session_vwap,
    sma,
)

__all__ = [
    'IndicatorParams',
    'compute_indicators',
    'atr',
    'ema',
    'macd',
    'rsi',
    'session_vwap',
    'sma',
]
