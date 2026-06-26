"""Broker-vs-DB reconciliation (DECISIONS.md §4).

The broker is the source of truth. Every run reconciles the stored ``DailyState``
against the live broker position *before* deciding anything:

- DB says OPEN but the broker is flat → the stop/EOD already fired; mark the day
  CLOSED and do **not** re-sell.
- DB and broker agree → proceed to normal routing.
- They disagree in any other way (an unexpected position, a phantom position
  after CLOSED) → halt this run: do nothing and raise an alert. Never guess.

Pure function of (state, broker position) → outcome; fully unit-testable.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from trading_bot.broker.base import BrokerPosition
from trading_bot.state.models import PositionStatus


class ReconcileAction(StrEnum):
    PROCEED = 'PROCEED'  # consistent — continue routing by status
    MARK_CLOSED = 'MARK_CLOSED'  # broker flat while DB open — close out the day
    HALT = 'HALT'  # disagreement — do nothing + alert


class ReconcileResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: ReconcileAction
    status: PositionStatus  # authoritative status to act on
    alert: str | None = None


def reconcile(state_status: PositionStatus, position: BrokerPosition | None) -> ReconcileResult:
    if state_status is PositionStatus.NO_POSITION:
        if position is None:
            return ReconcileResult(
                action=ReconcileAction.PROCEED, status=PositionStatus.NO_POSITION
            )
        return ReconcileResult(
            action=ReconcileAction.HALT,
            status=PositionStatus.NO_POSITION,
            alert=(
                f'Broker holds {position.symbol} but DB says NO_POSITION — '
                'unexpected position; doing nothing.'
            ),
        )

    if state_status is PositionStatus.POSITION_OPEN:
        if position is not None:
            return ReconcileResult(
                action=ReconcileAction.PROCEED, status=PositionStatus.POSITION_OPEN
            )
        # Open in DB but flat at broker: the bracket stop or EOD exit already fired.
        return ReconcileResult(
            action=ReconcileAction.MARK_CLOSED,
            status=PositionStatus.POSITION_CLOSED,
            alert='DB OPEN but broker flat — stop/EOD already fired; marking CLOSED.',
        )

    # state_status is POSITION_CLOSED (terminal for the day)
    if position is not None:
        return ReconcileResult(
            action=ReconcileAction.HALT,
            status=PositionStatus.POSITION_CLOSED,
            alert=(
                f'Broker still holds {position.symbol} after CLOSED — '
                'phantom position; doing nothing.'
            ),
        )
    return ReconcileResult(action=ReconcileAction.PROCEED, status=PositionStatus.POSITION_CLOSED)
