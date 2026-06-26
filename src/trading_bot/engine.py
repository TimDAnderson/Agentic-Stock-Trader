"""The deterministic run engine (DECISIONS.md §4).

Every run does: **reconcile → route by state → decide → conditional-write**.
This is the plumbing that wires a `Strategy` decision to the `Broker` while the
`StateRepository` gates transitions so overlapping/retried runs never double-buy.
The phase-5 LangGraph run graph will *mirror* this state machine, not replace it.

The engine takes a pre-assembled `MarketState` (data gathering lives in the data
tools / LangGraph layer). It is a pure orchestrator over injected dependencies —
no global state, no network beyond the broker/repository it's handed — so it runs
identically against `FakeBroker` + `InMemoryStateRepository` (tests) and the real
Alpaca + DynamoDB stack.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from trading_bot.broker.base import BracketOrder, Broker, BrokerPosition, OrderResult
from trading_bot.broker.errors import DuplicateClientOrderIdError
from trading_bot.broker.ids import entry_id, exit_id, moc_id
from trading_bot.domain.config import StrategyConfig
from trading_bot.domain.decisions import EntryDecision, ExitAction
from trading_bot.domain.market_state import MarketState, Position
from trading_bot.market_calendar import MarketCalendar, is_market_open
from trading_bot.reasoning.advisor import Advisor, Advisory
from trading_bot.reasoning.veto import apply_advisory
from trading_bot.state.errors import ConcurrentTransitionError, StateAlreadyExistsError
from trading_bot.state.models import DailyState, PositionStatus, RunRecord, TradeRecord
from trading_bot.state.reconcile import ReconcileAction, reconcile
from trading_bot.state.repository import StateRepository
from trading_bot.strategy.base import Strategy

ET = ZoneInfo('America/New_York')


def _trade_date(as_of: datetime) -> date:
    """The ET calendar date that owns this run (state is keyed by it)."""
    dt = as_of.astimezone(ET) if as_of.tzinfo is not None else as_of
    return dt.date()


def _et_time(as_of: datetime) -> time:
    """Time-of-day in ET (naive ``as_of`` is assumed already ET)."""
    dt = as_of.astimezone(ET) if as_of.tzinfo is not None else as_of
    return dt.timetz().replace(tzinfo=None)


class TradingEngine:
    """Orchestrates one run of the buy-once / sell-once state machine."""

    def __init__(
        self,
        broker: Broker,
        repository: StateRepository,
        strategy: Strategy,
        config: StrategyConfig,
        *,
        advisor: Advisor | None = None,
        calendar: MarketCalendar | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.broker = broker
        self.repository = repository
        self.strategy = strategy
        self.config = config
        self.advisor = advisor
        self.calendar = calendar
        self._clock = clock or (lambda: datetime.now(UTC))

    def run(self, state: MarketState, *, trade_date: date | None = None) -> RunRecord:
        td = trade_date or _trade_date(state.as_of)

        # Market-open gate first (DECISIONS.md §4): closed/holiday/half-day → do
        # nothing, before any broker or DB write. Does not create a state row.
        if self.calendar is not None and not is_market_open(self.calendar, state.as_of):
            existing = self.repository.get_daily_state(td)
            status = existing.status if existing else PositionStatus.NO_POSITION
            return self._record(
                td,
                status_before=status,
                status_after=status,
                action='DO_NOTHING',
                reason='Market closed (calendar gate).',
                state=state,
            )

        daily = self._get_or_create(td)

        # Reconcile against the broker (source of truth) before deciding anything.
        positions = self.broker.get_positions()
        position = positions[0] if positions else None
        result = reconcile(daily.status, position)

        if result.action is ReconcileAction.MARK_CLOSED:
            after = self._try_transition(daily, PositionStatus.POSITION_CLOSED)
            return self._record(
                td,
                status_before=daily.status,
                status_after=after,
                action='RECONCILE',
                reason=result.alert or 'Reconciled to broker.',
                state=state,
                alert=result.alert,
            )
        if result.action is ReconcileAction.HALT:
            return self._record(
                td,
                status_before=daily.status,
                status_after=daily.status,
                action='DO_NOTHING',
                reason=result.alert or 'Reconcile halt.',
                state=state,
                alert=result.alert,
            )

        # Consistent — route by the authoritative status.
        if result.status is PositionStatus.NO_POSITION:
            return self._enter(daily, state, td)
        if result.status is PositionStatus.POSITION_OPEN and position is not None:
            return self._exit(daily, state, td, position)
        return self._record(
            td,
            status_before=result.status,
            status_after=result.status,
            action='DO_NOTHING',
            reason='Position closed for the day; nothing to do.',
            state=state,
        )

    # -- entry --------------------------------------------------------------
    def _enter(self, daily: DailyState, state: MarketState, td: date) -> RunRecord:
        decision = self.strategy.evaluate_entry(state, self.config)

        # Advisory pass (veto-only): the LLM may downgrade a buy to do-nothing,
        # never the reverse. The deterministic rule still holds the wheel.
        advisory: Advisory | None = None
        if decision.action.is_buy and self.advisor is not None:
            advisory = self.advisor.advise(state, decision)
            decision = apply_advisory(decision, advisory)

        if not decision.action.is_buy:
            return self._record(
                td,
                status_before=PositionStatus.NO_POSITION,
                status_after=PositionStatus.NO_POSITION,
                action=decision.action.value,
                reason=decision.reason,
                state=state,
                advisory=advisory,
            )

        symbol = self.config.instruments.symbol_for(decision.action)
        assert symbol is not None
        assert decision.qty is not None and decision.stop_loss_price is not None
        coid = entry_id(td)

        # Write intent (reserve the slot) BEFORE the order — a conditional write
        # gated on NO_POSITION. If a concurrent run already reserved it, stop.
        reserved = daily.transitioned(
            PositionStatus.POSITION_OPEN,
            self._clock(),
            symbol=symbol,
            qty=decision.qty,
            entry_order_id=coid,
        )
        try:
            self.repository.transition_status(PositionStatus.NO_POSITION, reserved)
        except ConcurrentTransitionError:
            current = self.repository.get_daily_state(td)
            return self._record(
                td,
                status_before=PositionStatus.NO_POSITION,
                status_after=current.status if current else PositionStatus.POSITION_OPEN,
                action='DO_NOTHING',
                reason='Entry slot already taken by a concurrent run.',
                state=state,
                alert='Concurrent entry detected; did not double-buy.',
            )

        # Submit the idempotent bracket order; a duplicate id means it already went in.
        order = BracketOrder(
            symbol=symbol,
            qty=decision.qty,
            stop_loss_price=decision.stop_loss_price,
            take_profit_price=decision.take_profit_price,
            client_order_id=coid,
        )
        try:
            fill: OrderResult | None = self.broker.submit_bracket_buy(order)
        except DuplicateClientOrderIdError:
            fill = self.broker.get_order_by_client_id(coid)

        if fill is not None:
            self.repository.append_trade(self._entry_trade(td, fill, decision))
        return self._record(
            td,
            status_before=PositionStatus.NO_POSITION,
            status_after=PositionStatus.POSITION_OPEN,
            action=decision.action.value,
            reason=decision.reason,
            state=state,
            alert=None if fill is not None else 'Order submitted but no result returned.',
            advisory=advisory,
        )

    # -- exit ---------------------------------------------------------------
    def _exit(
        self, daily: DailyState, state: MarketState, td: date, position: BrokerPosition
    ) -> RunRecord:
        held = Position(
            symbol=position.symbol,
            qty=position.qty,
            avg_entry_price=position.avg_entry_price,
            current_price=position.current_price or position.avg_entry_price,
        )
        decision = self.strategy.evaluate_exit(state, held, self.config)
        if decision.action is ExitAction.HOLD:
            # Before holding, consider the EOD market-on-close backstop (§5): if
            # we're in the afternoon window and haven't placed it yet, rest a MOC
            # sell so the close flattens us even if no later run fires.
            moc_record = self._maybe_place_moc(daily, state, td, position)
            if moc_record is not None:
                return moc_record
            return self._record(
                td,
                status_before=PositionStatus.POSITION_OPEN,
                status_after=PositionStatus.POSITION_OPEN,
                action=decision.action.value,
                reason=decision.reason,
                state=state,
            )

        # SELL: liquidate the position and cancel the resting bracket legs (§5).
        results = self.broker.close_all_positions(cancel_orders=True)
        coid = exit_id(td)
        closed = daily.transitioned(
            PositionStatus.POSITION_CLOSED, self._clock(), exit_order_id=coid
        )
        try:
            self.repository.transition_status(PositionStatus.POSITION_OPEN, closed)
        except ConcurrentTransitionError:
            current = self.repository.get_daily_state(td)
            return self._record(
                td,
                status_before=PositionStatus.POSITION_OPEN,
                status_after=current.status if current else PositionStatus.POSITION_CLOSED,
                action=ExitAction.HOLD.value,
                reason='Exit already handled by a concurrent run.',
                state=state,
            )

        for r in results:
            if r.symbol == position.symbol:
                exit_price = r.filled_avg_price or held.current_price
                pnl = (exit_price - position.avg_entry_price) * position.qty
                self.repository.append_trade(self._exit_trade(td, r, coid, pnl))
        return self._record(
            td,
            status_before=PositionStatus.POSITION_OPEN,
            status_after=PositionStatus.POSITION_CLOSED,
            action=decision.action.value,
            reason=decision.reason,
            state=state,
        )

    # -- EOD market-on-close backstop (§5) ----------------------------------
    def _maybe_place_moc(
        self, daily: DailyState, state: MarketState, td: date, position: BrokerPosition
    ) -> RunRecord | None:
        """Rest a MOC sell once, in the afternoon window. Returns a run record if
        it acted, else ``None`` (the caller then records a normal HOLD).

        Stays ``POSITION_OPEN`` — the MOC fills at the close; a later run (or the
        next day) reconciles to ``POSITION_CLOSED`` when the broker shows flat.
        """
        cfg = self.config
        if not cfg.moc_backstop_enabled or daily.moc_order_id is not None:
            return None
        now = _et_time(state.as_of)
        if not (cfg.place_moc_after <= now < cfg.moc_cutoff):
            return None

        coid = moc_id(td)
        # Drop the resting bracket legs first: a stop that fills after the MOC is
        # placed would leave the MOC selling shares we no longer hold (a short).
        self.broker.cancel_all_orders()
        try:
            fill: OrderResult | None = self.broker.submit_moc_sell(
                position.symbol, position.qty, coid
            )
        except DuplicateClientOrderIdError:
            fill = self.broker.get_order_by_client_id(coid)

        updated = daily.transitioned(PositionStatus.POSITION_OPEN, self._clock(), moc_order_id=coid)
        try:
            self.repository.transition_status(PositionStatus.POSITION_OPEN, updated)
        except ConcurrentTransitionError:
            return self._record(
                td,
                status_before=PositionStatus.POSITION_OPEN,
                status_after=PositionStatus.POSITION_OPEN,
                action=ExitAction.HOLD.value,
                reason='MOC backstop already placed by a concurrent run.',
                state=state,
            )

        if fill is not None:
            self.repository.append_trade(self._moc_trade(td, fill))
        return self._record(
            td,
            status_before=PositionStatus.POSITION_OPEN,
            status_after=PositionStatus.POSITION_OPEN,
            action='MOC',
            reason=(
                f'EOD backstop: resting MOC sell for {position.qty} {position.symbol} '
                f'(fills at the close); bracket legs cancelled.'
            ),
            state=state,
            alert=None if fill is not None else 'MOC submitted but no result returned.',
        )

    def _moc_trade(self, td: date, fill: OrderResult) -> TradeRecord:
        return TradeRecord(
            trade_date=td,
            order_id=fill.client_order_id,
            broker_order_id=fill.id,
            kind='moc',
            symbol=fill.symbol,
            side=fill.side,
            qty=fill.qty,
            status=fill.status,
            filled_qty=fill.filled_qty,
            filled_avg_price=fill.filled_avg_price,
            strategy_version=self.strategy.version,
            mode=self.broker.mode,
            submitted_at=fill.submitted_at,
        )

    # -- helpers ------------------------------------------------------------
    def _get_or_create(self, td: date) -> DailyState:
        existing = self.repository.get_daily_state(td)
        if existing is not None:
            return existing
        now = self._clock()
        fresh = DailyState(
            trade_date=td,
            status=PositionStatus.NO_POSITION,
            strategy_version=self.strategy.version,
            mode=self.broker.mode,
            created_at=now,
            updated_at=now,
        )
        try:
            return self.repository.create_daily_state(fresh)
        except StateAlreadyExistsError:
            current = self.repository.get_daily_state(td)
            assert current is not None  # a concurrent run created it
            return current

    def _try_transition(self, daily: DailyState, status: PositionStatus) -> PositionStatus:
        updated = daily.transitioned(status, self._clock())
        try:
            self.repository.transition_status(daily.status, updated)
            return status
        except ConcurrentTransitionError:
            current = self.repository.get_daily_state(daily.trade_date)
            return current.status if current else daily.status

    def _entry_trade(self, td: date, fill: OrderResult, decision: EntryDecision) -> TradeRecord:
        return TradeRecord(
            trade_date=td,
            order_id=fill.client_order_id,
            broker_order_id=fill.id,
            kind='entry',
            symbol=fill.symbol,
            side=fill.side,
            qty=fill.qty,
            status=fill.status,
            filled_qty=fill.filled_qty,
            filled_avg_price=fill.filled_avg_price,
            stop_loss_price=decision.stop_loss_price,
            take_profit_price=decision.take_profit_price,
            strategy_version=self.strategy.version,
            mode=self.broker.mode,
            submitted_at=fill.submitted_at,
        )

    def _exit_trade(self, td: date, fill: OrderResult, coid: str, pnl: float) -> TradeRecord:
        return TradeRecord(
            trade_date=td,
            order_id=coid,
            broker_order_id=fill.id,
            kind='exit',
            symbol=fill.symbol,
            side=fill.side,
            qty=fill.qty,
            status=fill.status,
            filled_qty=fill.filled_qty,
            filled_avg_price=fill.filled_avg_price,
            pnl=pnl,
            strategy_version=self.strategy.version,
            mode=self.broker.mode,
            submitted_at=fill.submitted_at,
        )

    def _snapshot(self, state: MarketState) -> dict[str, object]:
        snap: dict[str, object] = {'as_of': state.as_of.isoformat(), 'equity': state.equity}
        ref = state.indicators_for(self.config.instruments.reference_symbol)
        if ref is not None:
            snap['reference'] = {
                'symbol': ref.symbol,
                'price': ref.price,
                'rsi': ref.rsi,
                'macd_hist': ref.macd_hist,
                'vwap': ref.vwap,
            }
        return snap

    def _record(
        self,
        trade_date: date,
        *,
        status_before: PositionStatus,
        status_after: PositionStatus,
        action: str,
        reason: str,
        state: MarketState,
        alert: str | None = None,
        advisory: Advisory | None = None,
    ) -> RunRecord:
        run = RunRecord(
            trade_date=trade_date,
            ts=self._clock(),
            action=action,
            reason=reason,
            status_before=status_before,
            status_after=status_after,
            strategy_version=self.strategy.version,
            mode=self.broker.mode,
            market_snapshot=self._snapshot(state),
            advisory=advisory.model_dump(mode='json') if advisory else None,
            llm_calls=advisory.llm_calls if advisory else None,
            alert=alert,
        )
        self.repository.append_run(run)
        return run
