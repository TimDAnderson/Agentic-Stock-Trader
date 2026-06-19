"""All strategy tunables in one place (DECISIONS.md §6, §10).

Loaded from SSM Parameter Store / DynamoDB at runtime so tuning is a
**zero-deploy** config change. Kept in git for history + review; never edited
live in the console. ``version`` is stamped on every trade record for
attribution and rollback.

A pydantic model: loading from a dict (local JSON/YAML, SSM JSON, or a DynamoDB
item) is validated and type-coerced for free, and ``"HH:MM"`` strings parse
straight into ``datetime.time``.
"""

from __future__ import annotations

from datetime import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from trading_bot.domain.decisions import EntryAction


class InstrumentConfig(BaseModel):
    """Which tickers the bot trades, decoupled from the strategy rule.

    The directional view is read from ``reference_symbol`` (an unleveraged index
    proxy, normally QQQ). A bullish view buys ``bullish_symbol``; a bearish view
    buys ``bearish_symbol`` (an inverse ETF). Swapping these is how you move
    between the QQQ/PSQ and TQQQ/SQQQ instrument sets without touching code.
    """

    model_config = ConfigDict(frozen=True)

    reference_symbol: str = 'QQQ'  # signal source for the bull/bear view
    bullish_symbol: str = 'QQQ'  # bought on a bullish view
    bearish_symbol: str = 'PSQ'  # bought on a bearish view (an inverse ETF)

    @classmethod
    def qqq_psq(cls) -> InstrumentConfig:
        """Default unleveraged pair."""
        return cls(reference_symbol='QQQ', bullish_symbol='QQQ', bearish_symbol='PSQ')

    @classmethod
    def tqqq_sqqq(cls) -> InstrumentConfig:
        """3x leveraged pair. View is still read from QQQ; TQQQ/SQQQ are traded.

        Leveraged ETFs swing ~3x and decay faster — materially higher risk
        (DECISIONS.md §14). Stops scale with their ATR, but size accordingly.
        """
        return cls(reference_symbol='QQQ', bullish_symbol='TQQQ', bearish_symbol='SQQQ')

    def symbol_for(self, action: EntryAction) -> str | None:
        """Resolve a directional action to the ticker to buy (or ``None``)."""
        if action is EntryAction.BUY_BULLISH:
            return self.bullish_symbol
        if action is EntryAction.BUY_BEARISH:
            return self.bearish_symbol
        return None

    def tradable_symbols(self) -> tuple[str, str]:
        return (self.bullish_symbol, self.bearish_symbol)


class StrategyConfig(BaseModel):
    """Tunables for the deterministic strategy.

    Times are **market-local (ET)** ``datetime.time`` values; the caller is
    responsible for comparing them against an ET-localized ``as_of``.
    """

    model_config = ConfigDict(frozen=True, extra='ignore')

    version: str = 'v1'

    # Which instrument set to trade (QQQ/PSQ by default; see InstrumentConfig).
    instruments: InstrumentConfig = Field(default_factory=InstrumentConfig)

    # --- Entry gating -------------------------------------------------------
    # No new entries after this time-of-day (ET). Late runs may only manage/sell.
    no_entry_after: time = time(14, 30)
    # Don't trade into a high-impact macro event within this many minutes.
    no_trade_window_minutes_before_event: float = 60.0
    high_impact_levels: tuple[str, ...] = ('high',)

    # --- Conviction filters -------------------------------------------------
    min_relative_volume: float = 1.0  # require at-or-above average participation
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    # ATR floor as a fraction of price — below this the move is too quiet to risk.
    min_atr_pct: float = 0.001

    # --- Sizing & risk ------------------------------------------------------
    # Target dollars to deploy on one entry (rounded down to whole shares). When
    # set it overrides max_position_pct; leave None to size as a % of equity.
    target_position_usd: float | None = None
    max_position_pct: float = 0.10  # fraction of equity to deploy on one entry
    stop_loss_atr_multiple: float = 1.5
    take_profit_atr_multiple: float = 3.0

    # --- Exit management ----------------------------------------------------
    # Strategy-level forced exit time (ET). The scheduled EOD liquidation run is
    # the plumbing backstop; this lets the strategy ask to sell earlier.
    force_exit_after: time = time(15, 55)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StrategyConfig:
        """Build from a loosely-typed dict (SSM JSON, DynamoDB item, YAML).

        Accepts ``"HH:MM"`` strings for time fields and ignores unknown keys so
        config can carry metadata the strategy doesn't read.
        """
        return cls.model_validate(data)

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly dict (times as strings, tuples as lists)."""
        return self.model_dump(mode='json')
