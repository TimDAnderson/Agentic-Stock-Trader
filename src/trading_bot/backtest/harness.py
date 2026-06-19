"""Event-driven daily backtester for the buy-once / sell-once strategy.

For each trading day the harness walks decision timestamps in order, rebuilds a
``MarketState`` from data ``<= as_of`` (look-ahead safe), and routes by the same
NO_POSITION -> POSITION_OPEN -> POSITION_CLOSED machine the live bot uses:

- NO_POSITION: run ``evaluate_entry``. A buy fills at the as_of close and arms a
  bracket (stop + take-profit). Only one entry per day.
- POSITION_OPEN: between decision points, scan the held symbol's bars for a
  stop/take touch (stop assumed first if a bar straddles both — conservative);
  otherwise run ``evaluate_exit``.
- End of day: force-close any open position at the last price (the EOD
  liquidation backstop).

Fills are intentionally optimistic (see package docstring). Sizing uses live
equity so the equity curve compounds.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from trading_bot.domain.config import StrategyConfig
from trading_bot.domain.decisions import EntryAction, ExitAction
from trading_bot.domain.market_state import (
    EconomicEvent,
    Indicators,
    MarketContext,
    MarketState,
    NewsItem,
    Position,
)
from trading_bot.indicators.compute import IndicatorParams, compute_indicators
from trading_bot.strategy.base import Strategy

ET = ZoneInfo('America/New_York')


class Trade(BaseModel):
    """A completed round trip."""

    model_config = ConfigDict(frozen=True)

    trade_date: date
    symbol: str
    action: EntryAction
    qty: int
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    stop_loss_price: float
    exit_kind: str  # "stop" | "take" | "strategy" | "eod"
    strategy_version: str
    entry_reason: str
    exit_reason: str

    @property
    def pnl(self) -> float:
        return (self.exit_price - self.entry_price) * self.qty

    @property
    def return_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0
        return (self.exit_price - self.entry_price) / self.entry_price


class BacktestResult(BaseModel):
    """Mutable accumulator for a backtest run; trades and curve grow as it runs."""

    start_equity: float
    end_equity: float
    trades: list[Trade] = Field(default_factory=list)
    equity_curve: list[tuple[date, float]] = Field(default_factory=list)
    do_nothing_days: int = 0
    trading_days: int = 0

    @property
    def total_return_pct(self) -> float:
        if self.start_equity == 0:
            return 0.0
        return (self.end_equity - self.start_equity) / self.start_equity

    @property
    def num_trades(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.pnl > 0)

    @property
    def win_rate(self) -> float:
        return self.wins / self.num_trades if self.trades else 0.0

    def summary(self) -> str:
        return (
            f'days={self.trading_days} trades={self.num_trades} '
            f'do_nothing_days={self.do_nothing_days} '
            f'win_rate={self.win_rate:.0%} '
            f'return={self.total_return_pct:+.2%} '
            f'equity ${self.start_equity:,.0f} -> ${self.end_equity:,.0f}'
        )


class Backtester:
    """Replay historical OHLCV through a strategy.

    ``data`` maps symbol -> tz-aware OHLCV frame (must include the reference
    symbol QQQ and any tradable symbol, i.e. PSQ). ``decision_step`` subsamples
    decision points (e.g. 5 == evaluate every 5th bar) to keep runs fast.
    """

    def __init__(
        self,
        data: dict[str, pd.DataFrame],
        strategy: Strategy,
        config: StrategyConfig,
        *,
        start_equity: float = 100_000.0,
        decision_step: int = 1,
        params: IndicatorParams | None = None,
        news: Sequence[NewsItem] = (),
        events: Sequence[EconomicEvent] = (),
        tz: ZoneInfo = ET,
    ) -> None:
        required = {config.instruments.reference_symbol, *config.instruments.tradable_symbols()}
        missing = sorted(required - set(data))
        if missing:
            raise ValueError(f'data is missing symbols required by the config: {missing}.')
        self.data = {sym: df.sort_index() for sym, df in data.items()}
        self.strategy = strategy
        self.config = config
        self.start_equity = start_equity
        self.decision_step = max(1, decision_step)
        self.params = params or IndicatorParams()
        self.news = tuple(news)
        self.events = tuple(events)
        self.tz = tz

    def run(self) -> BacktestResult:
        equity = self.start_equity
        result = BacktestResult(start_equity=self.start_equity, end_equity=equity)

        for day, decision_times in self._iter_days():
            result.trading_days += 1
            day_outcome = self._run_day(day, decision_times, equity)
            if day_outcome is None:
                result.do_nothing_days += 1
            else:
                result.trades.append(day_outcome)
                equity += day_outcome.pnl
            result.equity_curve.append((day, equity))

        result.end_equity = equity
        return result

    def _iter_days(self) -> list[tuple[date, list[pd.Timestamp]]]:
        """Per-day ordered decision timestamps, drawn from the reference symbol."""
        ref = self.data[self.config.instruments.reference_symbol]
        sessions = pd.Series(ref.index.tz_convert(self.tz).date, index=ref.index)
        out: list[tuple[date, list[pd.Timestamp]]] = []
        for day, idx in ref.groupby(sessions).groups.items():
            ordered = list(pd.DatetimeIndex(idx).sort_values())
            sampled = ordered[:: self.decision_step]
            # Always keep the final bar so EOD logic / forced close can fire.
            if ordered and ordered[-1] not in sampled:
                sampled.append(ordered[-1])
            out.append((day, sampled))
        out.sort(key=lambda t: t[0])
        return out

    def _build_state(self, as_of: pd.Timestamp, equity: float) -> MarketState:
        indicators: dict[str, Indicators] = {}
        for sym, df in self.data.items():
            sliced = df.loc[df.index <= as_of]
            if sliced.empty:
                continue
            indicators[sym] = compute_indicators(
                df, sym, as_of.to_pydatetime(), self.params, self.tz
            )
        fresh_news = tuple(n for n in self.news if n.timestamp <= as_of.to_pydatetime())
        return MarketState(
            as_of=as_of.to_pydatetime(),
            indicators=indicators,
            equity=equity,
            context=MarketContext(),
            news=fresh_news,
            events=self.events,
        )

    def _run_day(
        self, day: date, decision_times: list[pd.Timestamp], equity: float
    ) -> Trade | None:
        position: _OpenPosition | None = None

        for i, as_of in enumerate(decision_times):
            state = self._build_state(as_of, equity)

            if position is None:
                decision = self.strategy.evaluate_entry(state, self.config)
                if decision.action.is_buy:
                    symbol = self.config.instruments.symbol_for(decision.action)
                    traded = state.indicators_for(symbol) if symbol else None
                    # A buy decision always carries symbol/qty/stop (enforced by
                    # EntryDecision); the guards narrow types for the checker.
                    if symbol is None or traded is None:
                        continue
                    assert decision.qty is not None and decision.stop_loss_price is not None
                    position = _OpenPosition(
                        symbol=symbol,
                        action=decision.action,
                        qty=decision.qty,
                        entry_time=as_of,
                        entry_price=traded.price,
                        stop_loss_price=decision.stop_loss_price,
                        take_profit_price=decision.take_profit_price,
                        entry_reason=decision.reason,
                    )
                continue

            # Position is open: first look for a stop/take touch since last check.
            prev = decision_times[i - 1]
            touched = self._scan_bracket(position, prev, as_of)
            if touched is not None:
                exit_price, exit_kind = touched
                return self._close(day, position, as_of, exit_price, exit_kind, 'bracket touched')

            held_ind = state.indicators_for(position.symbol)
            if held_ind is None:
                continue  # no indicators for the held symbol this run; can't manage it
            held = Position(
                symbol=position.symbol,
                qty=position.qty,
                avg_entry_price=position.entry_price,
                current_price=held_ind.price,
                stop_loss_price=position.stop_loss_price,
            )
            exit_decision = self.strategy.evaluate_exit(state, held, self.config)
            if exit_decision.action is ExitAction.SELL:
                return self._close(
                    day, position, as_of, held.current_price, 'strategy', exit_decision.reason
                )

        # End of day: force-close anything still open (EOD liquidation backstop).
        if position is not None:
            last_ts = decision_times[-1]
            last_price = float(self.data[position.symbol].loc[:last_ts, 'close'].iloc[-1])
            return self._close(day, position, last_ts, last_price, 'eod', 'EOD forced liquidation')

        return None

    def _scan_bracket(
        self, position: _OpenPosition, after: pd.Timestamp, until: pd.Timestamp
    ) -> tuple[float, str] | None:
        """Check the held symbol's bars in (after, until] for a stop/take touch.

        If a single bar straddles both levels, the stop is assumed to fill first
        (conservative). Returns (fill_price, kind) or None.
        """
        df = self.data[position.symbol]
        window = df.loc[(df.index > after) & (df.index <= until)]
        for _, bar in window.iterrows():
            if bar['low'] <= position.stop_loss_price:
                return position.stop_loss_price, 'stop'
            if position.take_profit_price is not None and bar['high'] >= position.take_profit_price:
                return position.take_profit_price, 'take'
        return None

    def _close(
        self,
        day: date,
        position: _OpenPosition,
        exit_time: pd.Timestamp,
        exit_price: float,
        exit_kind: str,
        exit_reason: str,
    ) -> Trade:
        return Trade(
            trade_date=day,
            symbol=position.symbol,
            action=position.action,
            qty=position.qty,
            entry_time=position.entry_time.to_pydatetime(),
            entry_price=position.entry_price,
            exit_time=exit_time.to_pydatetime(),
            exit_price=exit_price,
            stop_loss_price=position.stop_loss_price,
            exit_kind=exit_kind,
            strategy_version=self.strategy.version,
            entry_reason=position.entry_reason,
            exit_reason=exit_reason,
        )


@dataclass
class _OpenPosition:
    symbol: str
    action: EntryAction
    qty: int
    entry_time: pd.Timestamp
    entry_price: float
    stop_loss_price: float
    take_profit_price: float | None
    entry_reason: str
