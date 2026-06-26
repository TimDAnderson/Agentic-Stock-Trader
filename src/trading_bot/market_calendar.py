"""Market-calendar gate (DECISIONS.md §3, §4, §14).

"Only trade on days the market is open" is enforced here, not by cron. A coarse
EventBridge schedule narrows *when* runs fire; this gate decides whether the
market is actually open *now* — covering holidays, half-days, and DST. Every run
(local or Lambda) checks it first and does nothing when closed.

The engine depends on the ``MarketCalendar`` Protocol. ``StaticMarketCalendar``
backs tests/local; ``AlpacaMarketCalendar`` is the real adapter (Alpaca's
calendar API). ``is_market_open`` derives open/closed from the session so each
implementation only has to answer "what is the session for this day?".
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time
from typing import Any, Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

ET = ZoneInfo('America/New_York')

REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)


class MarketSession(BaseModel):
    """A single trading session, with ET-localized open/close (half-days included)."""

    model_config = ConfigDict(frozen=True)

    date: date
    open: datetime
    close: datetime

    def contains(self, now: datetime) -> bool:
        return self.open <= now <= self.close


@runtime_checkable
class MarketCalendar(Protocol):
    def session_for(self, day: date) -> MarketSession | None:
        """The session for ``day``, or ``None`` if the market is closed that day."""
        ...


def is_market_open(calendar: MarketCalendar, now: datetime) -> bool:
    """True if ``now`` falls inside ``day``'s session (evaluated in ET)."""
    et_now = now.astimezone(ET) if now.tzinfo is not None else now.replace(tzinfo=ET)
    session = calendar.session_for(et_now.date())
    return session is not None and session.contains(et_now)


def _session(day: date, open_t: time, close_t: time) -> MarketSession:
    return MarketSession(
        date=day,
        open=datetime.combine(day, open_t, ET),
        close=datetime.combine(day, close_t, ET),
    )


class StaticMarketCalendar:
    """Fixed calendar for tests/local — explicit sessions, or always-open."""

    def __init__(
        self, sessions: dict[date, MarketSession] | None = None, *, always_open: bool = False
    ) -> None:
        self._sessions = sessions or {}
        self._always_open = always_open

    def session_for(self, day: date) -> MarketSession | None:
        if self._always_open:
            return _session(day, time(0, 0), time(23, 59, 59))
        return self._sessions.get(day)

    @classmethod
    def regular(
        cls,
        days: Iterable[date],
        *,
        open_t: time = REGULAR_OPEN,
        close_t: time = REGULAR_CLOSE,
    ) -> StaticMarketCalendar:
        """Build regular-hours sessions for the given trading days."""
        return cls({d: _session(d, open_t, close_t) for d in days})


class AlpacaMarketCalendar:
    """Real calendar backed by Alpaca's calendar API (lazy alpaca-py import)."""

    def __init__(self, client: Any) -> None:
        # Accepts an alpaca-py TradingClient (share the broker's client if you have one).
        self._client = client

    def session_for(self, day: date) -> MarketSession | None:
        from alpaca.trading.requests import GetCalendarRequest

        days = self._client.get_calendar(GetCalendarRequest(start=day, end=day))
        for entry in days or []:
            if entry.date == day:
                # alpaca-py returns ``open``/``close`` as *naive* datetimes already
                # combining the day with the ET wall-clock time; just localize them.
                return MarketSession(
                    date=entry.date,
                    open=entry.open.replace(tzinfo=ET),
                    close=entry.close.replace(tzinfo=ET),
                )
        return None
