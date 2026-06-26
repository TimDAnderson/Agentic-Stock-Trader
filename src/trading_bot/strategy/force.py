"""Force-entry strategy — DEV / VALIDATION ONLY (never deploy).

Always issues a bullish BUY so you can watch the full buy → state → sell path
against the paper account (e.g. a pre-deploy sanity check). It bypasses **every**
conviction gate, so it must never run in a real/deployed context. ``version`` is
stamped ``force-entry`` on the trade record so forced trades are obvious in the
audit log.

Exit is delegated to a real strategy (default ``MomentumStrategy``), so the
forced position still manages/sells normally (momentum rollover, forced-exit
time). Sizing mirrors the live rule: ``target_position_usd`` if set, else
``max_position_pct`` of equity — but always at least one share so it trades.

The protective bracket is **deliberately wide** (a fixed % well outside intraday
noise), *not* the live ATR-based stop: a tight minute-ATR stop fills within
seconds and the position never survives long enough to watch the exit logic run.
Widen/narrow it via ``stop_pct`` / ``take_pct`` if you want to test a fill.
"""

from __future__ import annotations

import math

from trading_bot.domain.config import StrategyConfig
from trading_bot.domain.decisions import EntryAction, EntryDecision, ExitDecision
from trading_bot.domain.market_state import MarketState, Position
from trading_bot.strategy.base import Strategy
from trading_bot.strategy.momentum import MomentumStrategy

_DEFAULT_STOP_PCT = 0.05  # 5% below entry — wide enough to survive a test session
_DEFAULT_TAKE_PCT = 0.05  # 5% above entry — won't fill on intraday noise


class ForceEntryStrategy:
    """Always buys the bullish instrument. For manual validation only."""

    version = 'force-entry'

    def __init__(
        self,
        exit_delegate: Strategy | None = None,
        *,
        stop_pct: float = _DEFAULT_STOP_PCT,
        take_pct: float = _DEFAULT_TAKE_PCT,
    ) -> None:
        self._exit: Strategy = exit_delegate or MomentumStrategy()
        self._stop_pct = stop_pct
        self._take_pct = take_pct

    def evaluate_entry(self, state: MarketState, config: StrategyConfig) -> EntryDecision:
        symbol = config.instruments.bullish_symbol
        ind = state.indicators_for(symbol)
        if ind is None:
            return EntryDecision.do_nothing(
                f'force-entry: no indicators for {symbol} to size the trade.'
            )

        if config.target_position_usd is not None:
            notional = config.target_position_usd
        else:
            notional = state.equity * config.max_position_pct
        qty = max(1, math.floor(notional / ind.price))

        # Wide fixed-% bracket so the position persists for observation, rather
        # than the live ATR stop (which is far too tight on minute-bar ATR).
        stop = round(ind.price * (1 - self._stop_pct), 2)
        take = round(ind.price * (1 + self._take_pct), 2)

        return EntryDecision(
            action=EntryAction.BUY_BULLISH,
            qty=qty,
            stop_loss_price=stop,
            take_profit_price=take,
            reason=(
                f'FORCED entry (validation only): {qty} {symbol} @ ~${ind.price:.2f}, '
                f'wide stop ${stop:.2f} (-{self._stop_pct:.0%}), take ${take:.2f}.'
            ),
        )

    def evaluate_exit(
        self, state: MarketState, position: Position, config: StrategyConfig
    ) -> ExitDecision:
        return self._exit.evaluate_exit(state, position, config)
