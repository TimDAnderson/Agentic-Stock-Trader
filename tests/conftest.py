"""Shared test fixtures and synthetic OHLCV builders."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

ET = ZoneInfo('America/New_York')


def make_session_bars(
    day: datetime,
    closes: list[float],
    *,
    volume: float = 1_000.0,
    spread: float = 0.05,
    start: time = time(9, 30),
) -> pd.DataFrame:
    """Build 1-minute RTH OHLCV bars for one ET session from a close path.

    open == prior close (first bar opens at its own close), high/low straddle
    the close by ``spread``. Index is tz-aware ET.
    """
    n = len(closes)
    base = datetime(day.year, day.month, day.day, start.hour, start.minute, tzinfo=ET)
    index = pd.DatetimeIndex([base + pd.Timedelta(minutes=i) for i in range(n)])
    closes_arr = np.asarray(closes, dtype=float)
    opens = np.concatenate([[closes_arr[0]], closes_arr[:-1]])
    highs = np.maximum(opens, closes_arr) + spread
    lows = np.minimum(opens, closes_arr) - spread
    return pd.DataFrame(
        {
            'open': opens,
            'high': highs,
            'low': lows,
            'close': closes_arr,
            'volume': np.full(n, volume),
        },
        index=index,
    )


def trending_closes(start_price: float, n: int, drift: float) -> list[float]:
    """A smooth monotone-ish price path with a fixed per-bar drift."""
    return [start_price + drift * i for i in range(n)]


@pytest.fixture
def et() -> ZoneInfo:
    return ET
