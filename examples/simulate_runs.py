"""Generate sample run logs for a full trading day, including SELLs.

The deployed bot has mostly logged DO_NOTHING / veto runs, so real SELL records
are scarce. This drives the **real** ``TradingEngine`` with a ``FakeBroker`` through
a scripted day so you get authentic ``RunRecord``s for the exit path.

Selling is a *deterministic* ``MomentumStrategy.evaluate_exit`` decision — there is
no LLM advisory on exits — so these appear as **run summaries** (the CloudWatch /
``export_runs.py`` format), not in the advisory export. Two scenarios are emitted:

1. an intraday **momentum-rollover SELL**, and
2. an **EOD** path: a resting **MOC** placed in the afternoon, then the forced
   ``close_all_positions`` SELL near the close.

    uv run --extra broker python examples/simulate_runs.py --out examples/sample_runs.txt
"""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from trading_bot.broker import BrokerMode, FakeBroker
from trading_bot.domain.config import StrategyConfig
from trading_bot.domain.market_state import Indicators, MarketState
from trading_bot.engine import TradingEngine
from trading_bot.market_calendar import StaticMarketCalendar
from trading_bot.state import InMemoryStateRepository
from trading_bot.state.models import RunRecord
from trading_bot.strategy import MomentumStrategy

ET = ZoneInfo('America/New_York')


class _Clock:
    """Returns a settable 'now' so run timestamps trace the scripted day."""

    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now


def _qqq(price: float, vwap: float, ema: float, macd_hist: float, rsi: float) -> Indicators:
    return Indicators(
        symbol='QQQ',
        price=price,
        vwap=vwap,
        ema=ema,
        macd_hist=macd_hist,
        rsi=rsi,
        atr=1.2,
        relative_volume=1.5,
    )


def _fmt_ts(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def _entry(
    engine: TradingEngine, broker: FakeBroker, clock: _Clock, day: datetime, ind: Indicators
) -> RunRecord:
    """Run the engine for one scripted minute and return the resulting record."""
    clock.now = day.astimezone(UTC)
    broker.set_price('QQQ', ind.price)
    state = MarketState(as_of=day, indicators={'QQQ': ind}, equity=100_000.0)
    return engine.run(state)


def _engine(broker: FakeBroker) -> TradingEngine:
    return TradingEngine(
        broker=broker,
        repository=InMemoryStateRepository(),
        strategy=MomentumStrategy(),
        config=StrategyConfig(),
        calendar=StaticMarketCalendar(always_open=True),
        clock=_Clock(),
    )


def _render(records: list[RunRecord]) -> str:
    lines: list[str] = []
    for run in records:
        summary = {
            'mode': run.mode.value,
            'trade_date': run.trade_date.isoformat(),
            'action': run.action,
            'reason': run.reason,
            'status_before': run.status_before.value,
            'status_after': run.status_after.value,
            'alert': run.alert,
            'duration_ms': 1850.0,
        }
        lines.append(f'[INFO]\t{_fmt_ts(run.ts)}\t{uuid.uuid4()}')
        lines.append(json.dumps(summary, indent=4))
        lines.append('')
    return '\n'.join(lines)


def _scenario_rollover() -> list[RunRecord]:
    """Buy in the morning, hold, then SELL on a momentum rollover."""
    broker = FakeBroker(BrokerMode.PAPER, prices={'QQQ': 400.0})
    engine = _engine(broker)
    clock = engine._clock  # type: ignore[attr-defined]
    d = lambda h, m: datetime(2026, 6, 22, h, m, tzinfo=ET)  # noqa: E731
    return [
        _entry(engine, broker, clock, d(9, 45), _qqq(400.0, 399.0, 398.5, 0.30, 60.0)),  # BUY
        _entry(engine, broker, clock, d(10, 30), _qqq(402.0, 400.0, 399.5, 0.15, 61.0)),  # HOLD
        _entry(engine, broker, clock, d(12, 0), _qqq(403.0, 401.0, 400.5, 0.10, 58.0)),  # HOLD
        _entry(engine, broker, clock, d(13, 30), _qqq(404.0, 405.0, 404.5, -0.10, 47.0)),  # SELL
        _entry(engine, broker, clock, d(14, 0), _qqq(404.0, 405.0, 404.5, -0.10, 47.0)),  # CLOSED
    ]


def _scenario_eod() -> list[RunRecord]:
    """Buy, hold all day, place the EOD MOC, then the forced near-close SELL."""
    broker = FakeBroker(BrokerMode.PAPER, prices={'QQQ': 400.0})
    engine = _engine(broker)
    clock = engine._clock  # type: ignore[attr-defined]
    d = lambda h, m: datetime(2026, 6, 23, h, m, tzinfo=ET)  # noqa: E731
    return [
        _entry(engine, broker, clock, d(9, 45), _qqq(400.0, 399.0, 398.5, 0.30, 60.0)),  # BUY
        _entry(engine, broker, clock, d(11, 0), _qqq(401.0, 400.0, 399.5, 0.10, 57.0)),  # HOLD
        _entry(engine, broker, clock, d(15, 47), _qqq(402.0, 401.0, 400.5, 0.05, 55.0)),  # MOC
        _entry(engine, broker, clock, d(15, 56), _qqq(402.0, 401.0, 400.5, 0.05, 55.0)),  # SELL
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description='Generate sample run logs (incl. SELLs).')
    parser.add_argument('--out', default=None, help='write to this file instead of stdout')
    args = parser.parse_args()

    text = (
        '# Sample run logs — intraday momentum-rollover SELL\n\n'
        + _render(_scenario_rollover())
        + '\n# Sample run logs — EOD: resting MOC, then forced near-close SELL\n\n'
        + _render(_scenario_eod())
    )
    if args.out:
        with open(args.out, 'w') as fh:
            fh.write(text)
        print(f'Wrote sample runs to {args.out}')
    else:
        print(text)


if __name__ == '__main__':
    main()
