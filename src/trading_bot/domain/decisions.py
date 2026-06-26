"""Typed strategy decisions (DECISIONS.md §6).

Every decision carries a human-readable ``reason`` for the audit log. Entry and
exit are deliberately separate value types so the run graph (and the broker
layer) can route on them without re-deriving intent.

Pydantic models: invariants (a buy must carry qty + stop; DO_NOTHING must not)
are enforced at construction and raise ``ValidationError`` (a ``ValueError``).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator


class EntryAction(StrEnum):
    """The *direction* of the entry decision — not a specific ticker.

    ``BUY_BULLISH`` bets the market goes up (buy the bullish vehicle, e.g. QQQ or
    TQQQ); ``BUY_BEARISH`` bets it goes down (buy the inverse vehicle, e.g. PSQ or
    SQQQ). The actual ticker is resolved from ``InstrumentConfig``, so the same
    rule trades different instrument sets without code changes.

    ``DO_NOTHING`` is a first-class, frequently-chosen outcome — a missed entry
    is cheap, a bad entry is expensive (DECISIONS.md §1).
    """

    BUY_BULLISH = 'BUY_BULLISH'
    BUY_BEARISH = 'BUY_BEARISH'
    DO_NOTHING = 'DO_NOTHING'

    @property
    def is_buy(self) -> bool:
        return self in (EntryAction.BUY_BULLISH, EntryAction.BUY_BEARISH)


class ExitAction(StrEnum):
    SELL = 'SELL'
    HOLD = 'HOLD'


class EntryDecision(BaseModel):
    """Result of ``Strategy.evaluate_entry``.

    For a buy, ``qty`` and ``stop_loss_price`` must be populated (they seed the
    bracket order). For DO_NOTHING they stay ``None``.
    """

    model_config = ConfigDict(frozen=True)

    action: EntryAction
    reason: str
    qty: int | None = None
    stop_loss_price: float | None = None
    take_profit_price: float | None = None

    @model_validator(mode='after')
    def _check_invariants(self) -> EntryDecision:
        if self.action.is_buy:
            if not self.qty or self.qty <= 0:
                raise ValueError(f'{self.action} requires a positive qty, got {self.qty!r}')
            if self.stop_loss_price is None or self.stop_loss_price <= 0:
                raise ValueError(
                    f'{self.action} requires a positive stop_loss_price, '
                    f'got {self.stop_loss_price!r}'
                )
        elif self.qty is not None or self.stop_loss_price is not None:
            raise ValueError('DO_NOTHING must not carry qty / stop_loss_price')
        return self

    @classmethod
    def do_nothing(cls, reason: str) -> EntryDecision:
        return cls(action=EntryAction.DO_NOTHING, reason=reason)


class ExitDecision(BaseModel):
    """Result of ``Strategy.evaluate_exit``."""

    model_config = ConfigDict(frozen=True)

    action: ExitAction
    reason: str

    @classmethod
    def hold(cls, reason: str) -> ExitDecision:
        return cls(action=ExitAction.HOLD, reason=reason)

    @classmethod
    def sell(cls, reason: str) -> ExitDecision:
        return cls(action=ExitAction.SELL, reason=reason)
