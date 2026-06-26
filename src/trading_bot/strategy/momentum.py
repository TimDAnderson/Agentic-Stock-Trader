"""Deterministic intraday momentum strategy, v1.

Decision shape (DECISIONS.md §1): each run decide BUY_BULLISH / BUY_BEARISH /
DO_NOTHING. Biased hard toward DO_NOTHING — a missed entry is cheap, a bad entry
is expensive.

The *direction* view is formed on the reference index (``InstrumentConfig.
reference_symbol``, normally QQQ). A bullish view buys the configured bullish
vehicle (QQQ or TQQQ); a bearish view buys the inverse vehicle (PSQ or SQQQ) —
note we go *long* the inverse rather than shorting, so both positions are long
and both stops sit below entry. Sizing and stops always use the **traded**
symbol's own price/ATR.

Every branch returns a ``reason`` for the audit log. This module is a pure
function of (MarketState, StrategyConfig): no broker, no AWS, no LLM.
"""

from __future__ import annotations

import math
from datetime import time
from zoneinfo import ZoneInfo

from trading_bot.domain.config import StrategyConfig
from trading_bot.domain.decisions import EntryAction, EntryDecision, ExitDecision
from trading_bot.domain.market_state import MarketState, Position

ET = ZoneInfo('America/New_York')


def _et_time(state: MarketState) -> time:
    """Time-of-day in ET for the snapshot.

    A naive ``as_of`` is assumed to already be ET; an aware one is converted.
    """
    dt = state.as_of
    if dt.tzinfo is not None:
        dt = dt.astimezone(ET)
    return dt.timetz().replace(tzinfo=None)


class MomentumStrategy:
    """Trend-confirmation entry with conviction + macro-event gating."""

    version = 'v1'

    def evaluate_entry(self, state: MarketState, config: StrategyConfig) -> EntryDecision:
        # --- Gate 1: entry cutoff. Late runs may only manage/sell. -----------
        now = _et_time(state)
        if now > config.no_entry_after:
            return EntryDecision.do_nothing(
                f'After entry cutoff {config.no_entry_after:%H:%M} ET (now {now:%H:%M}).'
            )

        # --- Gate 2: don't trade into a high-impact macro event. -------------
        upcoming = state.upcoming_events(config.no_trade_window_minutes_before_event)
        high_impact = [e for e in upcoming if e.impact in config.high_impact_levels]
        if high_impact:
            names = ', '.join(e.name for e in high_impact)
            return EntryDecision.do_nothing(
                f'High-impact event within '
                f'{config.no_trade_window_minutes_before_event:.0f}m: {names}.'
            )

        ref_symbol = config.instruments.reference_symbol
        ref = state.indicators_for(ref_symbol)
        if ref is None:
            return EntryDecision.do_nothing(f'No {ref_symbol} indicators in snapshot.')

        # --- Gate 3: conviction filters on the reference index. --------------
        missing = [
            n
            for n in ('vwap', 'ema', 'macd_hist', 'rsi', 'relative_volume')
            if getattr(ref, n) is None
        ]
        if missing:
            return EntryDecision.do_nothing(
                f'Insufficient history for {ref_symbol} ({", ".join(missing)}).'
            )
        # The guard above proves these are present; assert narrows for the checker.
        assert (
            ref.vwap is not None
            and ref.ema is not None
            and ref.macd_hist is not None
            and ref.rsi is not None
            and ref.relative_volume is not None
        )

        if ref.relative_volume < config.min_relative_volume:
            return EntryDecision.do_nothing(
                f'Relative volume {ref.relative_volume:.2f} below '
                f'min {config.min_relative_volume:.2f} — no conviction.'
            )

        if ref.atr is None or ref.atr <= 0 or (ref.atr / ref.price) < config.min_atr_pct:
            return EntryDecision.do_nothing(
                f'{ref_symbol} too quiet (ATR%% below {config.min_atr_pct:.3%}).'
            )

        # --- Direction: require trend agreement across vwap, ema, macd, rsi. -
        bullish = (
            ref.price > ref.vwap
            and ref.price > ref.ema
            and ref.macd_hist > 0
            and 50.0 < ref.rsi < config.rsi_overbought
        )
        bearish = (
            ref.price < ref.vwap
            and ref.price < ref.ema
            and ref.macd_hist < 0
            and config.rsi_oversold < ref.rsi < 50.0
        )

        if bullish:
            return self._build_buy(EntryAction.BUY_BULLISH, state, config)
        if bearish:
            return self._build_buy(EntryAction.BUY_BEARISH, state, config)

        return EntryDecision.do_nothing(
            f'Mixed {ref_symbol} signals (price/vwap/ema/macd/rsi not aligned; rsi={ref.rsi:.0f}).'
        )

    def _build_buy(
        self,
        action: EntryAction,
        state: MarketState,
        config: StrategyConfig,
    ) -> EntryDecision:
        """Size and bracket a long entry on the action's configured ticker.

        Sizing uses the *traded* symbol's own price/ATR. A target dollar amount
        (``target_position_usd``) takes precedence over the percent-of-equity
        size when set; either way the notional is rounded down to whole shares.
        """
        symbol = config.instruments.symbol_for(action)
        traded = state.indicators_for(symbol) if symbol else None
        if symbol is None or traded is None:
            return EntryDecision.do_nothing(
                f'{action.value}: no indicators for {symbol} to size the trade.'
            )
        if traded.atr is None or traded.atr <= 0:
            return EntryDecision.do_nothing(f'No usable ATR for {symbol}; cannot set a stop.')

        if config.target_position_usd is not None:
            notional = config.target_position_usd
        else:
            notional = state.equity * config.max_position_pct
        qty = math.floor(notional / traded.price)
        if qty < 1:
            return EntryDecision.do_nothing(
                f'Sizing yields <1 share of {symbol} '
                f'(notional ${notional:,.0f} / ${traded.price:.2f}).'
            )

        # Stop distance = the ATR-based stop, floored at a minimum % of price so a
        # tiny minute-bar ATR can't yield a sub-1% stop that noise trips instantly.
        # The take scales with the *actual* stop distance to keep the reward:risk
        # implied by the ATR multiples even when the floor is binding.
        rr_ratio = (
            config.take_profit_atr_multiple / config.stop_loss_atr_multiple
            if config.stop_loss_atr_multiple > 0
            else 2.0
        )
        atr_stop_distance = traded.atr * config.stop_loss_atr_multiple
        min_stop_distance = traded.price * config.min_stop_loss_pct
        stop_distance = max(atr_stop_distance, min_stop_distance)
        stop = round(traded.price - stop_distance, 2)
        take = round(traded.price + stop_distance * rr_ratio, 2)
        if stop <= 0:
            return EntryDecision.do_nothing(f'Computed stop {stop} for {symbol} is non-positive.')

        floored = min_stop_distance > atr_stop_distance
        basis = (
            f'{config.min_stop_loss_pct:.1%} floor'
            if floored
            else f'{config.stop_loss_atr_multiple}x ATR {traded.atr:.2f}'
        )
        return EntryDecision(
            action=action,
            qty=qty,
            stop_loss_price=stop,
            take_profit_price=take,
            reason=(
                f'{action.value}: {qty} {symbol} @ ~${traded.price:.2f} '
                f'(~${qty * traded.price:,.0f}), '
                f'stop ${stop:.2f} (-{stop_distance / traded.price:.2%}, {basis}), '
                f'take ${take:.2f}.'
            ),
        )

    def evaluate_exit(
        self, state: MarketState, position: Position, config: StrategyConfig
    ) -> ExitDecision:
        # Forced exit near close — defense in depth alongside the scheduled EOD
        # liquidation and the broker-side MOC backstop (DECISIONS.md §5).
        now = _et_time(state)
        if now >= config.force_exit_after:
            return ExitDecision.sell(
                f'At/after forced-exit {config.force_exit_after:%H:%M} ET (now {now:%H:%M}).'
            )

        # Momentum rollover on the held symbol → take the exit early. The hard
        # stop is the broker-side bracket; this is the strategy's discretionary exit.
        held = state.indicators_for(position.symbol)
        if held is not None and held.vwap is not None and held.macd_hist is not None:
            if held.price < held.vwap and held.macd_hist < 0:
                return ExitDecision.sell(
                    f'{position.symbol} momentum rolled over '
                    f'(price ${held.price:.2f} < vwap ${held.vwap:.2f}, macd_hist<0).'
                )

        return ExitDecision.hold(
            f'Holding {position.symbol} '
            f'(unrealized {position.unrealized_pl_pct:+.2%}); no exit trigger.'
        )


__all__ = ['MomentumStrategy']
