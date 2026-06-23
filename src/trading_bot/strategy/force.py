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
"""

from __future__ import annotations

import math

from trading_bot.domain.config import StrategyConfig
from trading_bot.domain.decisions import EntryAction, EntryDecision, ExitDecision
from trading_bot.domain.market_state import MarketState, Position
from trading_bot.strategy.base import Strategy
from trading_bot.strategy.momentum import MomentumStrategy

_FALLBACK_ATR_PCT = 0.01  # used when ATR isn't available yet (early session)


class ForceEntryStrategy:
    """Always buys the bullish instrument. For manual validation only."""

    version = 'force-entry'

    def __init__(self, exit_delegate: Strategy | None = None) -> None:
        self._exit: Strategy = exit_delegate or MomentumStrategy()

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

        atr = ind.atr if ind.atr and ind.atr > 0 else ind.price * _FALLBACK_ATR_PCT
        stop = round(ind.price - atr * config.stop_loss_atr_multiple, 2)
        if stop <= 0:
            stop = round(ind.price * (1 - _FALLBACK_ATR_PCT), 2)
        take = round(ind.price + atr * config.take_profit_atr_multiple, 2)

        return EntryDecision(
            action=EntryAction.BUY_BULLISH,
            qty=qty,
            stop_loss_price=stop,
            take_profit_price=take,
            reason=(
                f'FORCED entry (validation only): {qty} {symbol} @ ~${ind.price:.2f}, '
                f'stop ${stop:.2f}.'
            ),
        )

    def evaluate_exit(
        self, state: MarketState, position: Position, config: StrategyConfig
    ) -> ExitDecision:
        return self._exit.evaluate_exit(state, position, config)
