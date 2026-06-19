"""Tiny end-to-end demo: generate synthetic QQQ/PSQ bars and backtest the v1 rule.

Run with:  uv run python examples/run_backtest.py

This uses *synthetic* data so it runs with no API keys and no network — it
demonstrates the local strategy + backtest loop (DECISIONS.md §11, §13 steps 1-2).
Swap in real Alpaca bars later without touching the strategy or harness.
"""

from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from trading_bot.backtest import Backtester
from trading_bot.domain.config import StrategyConfig
from trading_bot.strategy import MomentumStrategy

ET = ZoneInfo('America/New_York')


def synth_session(
    day: datetime, start_price: float, drift: float, noise: float, seed: int
) -> pd.DataFrame:
    """One RTH session of 1-minute bars: drift + sinusoid + noise."""
    rng = np.random.default_rng(seed)
    n = 390  # minutes in a regular session
    t = np.arange(n)
    closes = start_price + drift * t + noise * np.sin(t / 25.0) + rng.normal(0, noise / 3, n)
    base = datetime(day.year, day.month, day.day, 9, 30, tzinfo=ET)
    index = pd.DatetimeIndex([base + pd.Timedelta(minutes=int(i)) for i in t])
    opens = np.concatenate([[closes[0]], closes[:-1]])
    return pd.DataFrame(
        {
            'open': opens,
            'high': np.maximum(opens, closes) + 0.05,
            'low': np.minimum(opens, closes) - 0.05,
            'close': closes,
            'volume': rng.uniform(800, 1200, n),
        },
        index=index,
    )


def main() -> None:
    days = [datetime(2026, 6, d) for d in (1, 2, 3, 4, 5)]
    qqq_frames, psq_frames = [], []
    for i, day in enumerate(days):
        drift = 0.01 if i % 2 == 0 else -0.01  # alternate up/down sessions
        qqq_frames.append(synth_session(day, 400.0, drift, noise=1.5, seed=i))
        # PSQ roughly mirrors QQQ intraday.
        psq_frames.append(synth_session(day, 12.0, -drift * 0.03, noise=0.08, seed=100 + i))

    data = {'QQQ': pd.concat(qqq_frames), 'PSQ': pd.concat(psq_frames)}

    bt = Backtester(
        data,
        strategy=MomentumStrategy(),
        config=StrategyConfig(),
        start_equity=100_000.0,
        decision_step=10,  # evaluate every 10 minutes
    )
    result = bt.run()

    print('Backtest (synthetic data):')
    print(' ', result.summary())
    for trade in result.trades:
        print(
            f'  {trade.trade_date} {trade.action.value:>11} x{trade.qty} '
            f'@ ${trade.entry_price:.2f} -> ${trade.exit_price:.2f} '
            f'[{trade.exit_kind}]  pnl ${trade.pnl:+,.0f}'
        )
    if not result.trades and not math.isnan(result.end_equity):
        print('  (no entries — the v1 rule declined every run on this data)')


if __name__ == '__main__':
    main()
