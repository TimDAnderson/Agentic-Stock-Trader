"""In-memory state repository for tests and offline local dev.

Faithfully models the conditional-write semantics (create-if-absent, guarded
transitions) so the engine's double-buy protection is exercised with no AWS.
"""

from __future__ import annotations

from datetime import date

from trading_bot.state.errors import ConcurrentTransitionError, StateAlreadyExistsError
from trading_bot.state.models import DailyState, PositionStatus, RunRecord, TradeRecord
from trading_bot.state.repository import StateRepository


class InMemoryStateRepository(StateRepository):
    def __init__(self) -> None:
        self._states: dict[date, DailyState] = {}
        self._runs: dict[date, list[RunRecord]] = {}
        self._trades: dict[date, list[TradeRecord]] = {}

    def get_daily_state(self, trade_date: date) -> DailyState | None:
        return self._states.get(trade_date)

    def create_daily_state(self, state: DailyState) -> DailyState:
        if state.trade_date in self._states:
            raise StateAlreadyExistsError(f'Daily state for {state.trade_date} already exists.')
        self._states[state.trade_date] = state
        return state

    def transition_status(self, expected: PositionStatus, updated: DailyState) -> DailyState:
        current = self._states.get(updated.trade_date)
        if current is None or current.status is not expected:
            found = None if current is None else current.status
            raise ConcurrentTransitionError(
                f'Expected status {expected} for {updated.trade_date}, found {found}.'
            )
        self._states[updated.trade_date] = updated
        return updated

    def append_run(self, run: RunRecord) -> None:
        self._runs.setdefault(run.trade_date, []).append(run)

    def append_trade(self, trade: TradeRecord) -> None:
        self._trades.setdefault(trade.trade_date, []).append(trade)

    def list_runs(self, trade_date: date) -> list[RunRecord]:
        return list(self._runs.get(trade_date, []))

    def list_trades(self, trade_date: date) -> list[TradeRecord]:
        return list(self._trades.get(trade_date, []))

    def list_trades_by_version(self, strategy_version: str) -> list[TradeRecord]:
        out: list[TradeRecord] = []
        for trades in self._trades.values():
            out.extend(t for t in trades if t.strategy_version == strategy_version)
        return out
