"""The persistence interface (DECISIONS.md §4, §9).

The engine depends on this Protocol, not on DynamoDB. ``InMemoryStateRepository``
backs tests/local dev; ``DynamoStateRepository`` is the deployed implementation.
Status transitions are **guarded** (conditional writes) so overlapping/retried
runs can never double-enter.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from trading_bot.state.models import DailyState, PositionStatus, RunRecord, TradeRecord


@runtime_checkable
class StateRepository(Protocol):
    def get_daily_state(self, trade_date: date) -> DailyState | None: ...

    def create_daily_state(self, state: DailyState) -> DailyState:
        """Create the row only if it doesn't exist (conditional create).

        Raises ``StateAlreadyExistsError`` if another run created it first.
        """
        ...

    def transition_status(self, expected: PositionStatus, updated: DailyState) -> DailyState:
        """Move the row to ``updated`` only if its current status == ``expected``.

        Raises ``ConcurrentTransitionError`` if the guard fails. This is the
        double-buy gate from DECISIONS.md §4.
        """
        ...

    def append_run(self, run: RunRecord) -> None: ...

    def append_trade(self, trade: TradeRecord) -> None: ...

    def list_runs(self, trade_date: date) -> list[RunRecord]: ...

    def list_trades(self, trade_date: date) -> list[TradeRecord]: ...

    def list_trades_by_version(self, strategy_version: str) -> list[TradeRecord]:
        """Cross-day query by strategy version (backed by a GSI on DynamoDB)."""
        ...
