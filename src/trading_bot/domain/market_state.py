"""The single, immutable input to a strategy decision (DECISIONS.md §6).

``MarketState`` is a snapshot with an ``as_of`` timestamp. **A strategy may only
use data at or before ``as_of``.** That rule is what structurally prevents
look-ahead bias and makes backtesting equal to replaying historical snapshots.

Everything here is *code-computed* and timestamped. There is deliberately **no
raw OHLCV and no live broker call** inside a MarketState — indicators are
computed upstream (see ``trading_bot.indicators``) and handed in as clean values.

These are frozen pydantic models, so they validate on construction and are
hashable/comparable value objects.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Indicators(BaseModel):
    """Code-computed signals for one symbol, all as-of the snapshot time.

    Values may be ``None`` when there is not enough history to compute them
    (e.g. ATR early in the session). The strategy must treat ``None`` as
    "no conviction" rather than guessing.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    price: float
    vwap: float | None = None
    sma: float | None = None
    ema: float | None = None
    rsi: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    atr: float | None = None
    relative_volume: float | None = None
    gap_pct: float | None = None


class MarketContext(BaseModel):
    """Broader regime context — not specific to one symbol."""

    model_config = ConfigDict(frozen=True)

    vix: float | None = None
    regime: str | None = None  # e.g. "risk_on" / "risk_off" / "neutral"
    premarket_futures_pct: float | None = None


class NewsItem(BaseModel):
    """A timestamped news headline. Freshness is everything (DECISIONS.md §8)."""

    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    headline: str
    source: str | None = None
    symbols: tuple[str, ...] = ()
    sentiment: float | None = None  # optional code-scored sentiment in [-1, 1]


class EconomicEvent(BaseModel):
    """A scheduled macro event (FOMC / CPI / jobs). Often the biggest mover."""

    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    name: str
    impact: str  # "high" | "medium" | "low"


class Position(BaseModel):
    """A currently-open broker position, as reconciled at snapshot time."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    qty: int
    avg_entry_price: float
    current_price: float
    stop_loss_price: float | None = None

    @property
    def unrealized_pl_pct(self) -> float:
        if self.avg_entry_price == 0:
            return 0.0
        return (self.current_price - self.avg_entry_price) / self.avg_entry_price


class MarketState(BaseModel):
    """Immutable decision input.

    ``indicators`` is keyed by symbol (expects "QQQ" and "PSQ"). ``equity`` is
    the account equity captured at ``as_of`` so position sizing stays a pure
    function — the strategy never calls the broker itself.
    """

    model_config = ConfigDict(frozen=True)

    as_of: datetime
    indicators: dict[str, Indicators]
    equity: float
    context: MarketContext = Field(default_factory=MarketContext)
    news: tuple[NewsItem, ...] = ()
    events: tuple[EconomicEvent, ...] = ()

    @model_validator(mode='after')
    def _reject_future_news(self) -> MarketState:
        # Enforce the as_of contract on the data we carry: nothing may be from
        # the future relative to the snapshot. This is the structural guard
        # against look-ahead bias.
        for item in self.news:
            if item.timestamp > self.as_of:
                raise ValueError(
                    f'NewsItem at {item.timestamp} is after as_of {self.as_of} '
                    '(look-ahead). Filter news to <= as_of before building MarketState.'
                )
        return self

    def indicators_for(self, symbol: str) -> Indicators | None:
        return self.indicators.get(symbol)

    def upcoming_events(self, within_minutes: float) -> list[EconomicEvent]:
        """Events scheduled to occur within ``within_minutes`` after ``as_of``.

        Past events are excluded; the strategy cares about events it is about to
        trade *into*.
        """
        horizon_seconds = within_minutes * 60.0
        out: list[EconomicEvent] = []
        for ev in self.events:
            delta = (ev.timestamp - self.as_of).total_seconds()
            if 0 <= delta <= horizon_seconds:
                out.append(ev)
        return out
