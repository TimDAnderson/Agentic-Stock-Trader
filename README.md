# Agentic-Stock-Trader

Intraday agentic QQQ/PSQ trading bot. Each morning it decides to **buy QQQ**,
**buy PSQ** (inverse), or **do nothing** — buy once, sell once, flat by close.

The full design, rationale, and standing caveats live in **[DECISIONS.md](DECISIONS.md)**.
Read it first; it is the source of truth for *why* things are shaped this way.

> **Not financial advice.** An automated bot trading real money carries real
> loss risk. Local/paper tests prove the system *works*, not that it makes money.

## Status

Build order follows DECISIONS.md §13. Done so far — the **fully local, no-AWS,
no-API-key** core:

- ✅ **1. Domain + Strategy interface + v1 rule** — `MarketState`, `StrategyConfig`,
  typed `Decisions`, the `Strategy` protocol, and a deterministic `MomentumStrategy`
  biased toward `DO_NOTHING`. Unit-tested.
- ✅ **2. Indicators + backtest harness** — RSI/EMA/SMA/VWAP/MACD/ATR/relative-volume/gap
  computed in code with **look-ahead protection**; an event-driven daily backtester
  enforcing buy-once/sell-once.

- ✅ **3. Broker layer (Alpaca paper flow)** — a `Broker` Protocol with typed
  order/account/position models, an in-memory `FakeBroker` (network-free tests),
  and an `AlpacaBroker` adapter (bracket orders, idempotent date-keyed
  `client_order_id`, `close_all_positions`, MOC backstop, startup mode
  verification). Run `examples/paper_smoke.py` against the paper endpoint.

- ✅ **4. State machine + persistence** — a `StateRepository` Protocol with an
  in-memory double and a DynamoDB single-table adapter (conditional-write gating,
  append-only run/trade records, GSI by strategy version), the §4 broker-vs-DB
  `reconcile` rules, and a `TradingEngine` that runs **reconcile → route → decide
  → conditional-write** (idempotent orders, no double-buy).

Not built yet (each layers on top without touching the above):

- ☐ 5. LangGraph reasoning — parallel gathering + ToT advisory (veto-only).
- ☐ 6. CDK deployment — paper + live stacks, EventBridge, Secrets Manager, alarms.

## Layout

```
src/trading_bot/
  domain/       # typed, pure data: MarketState, Decisions, StrategyConfig, Position
  indicators/   # code-computed signals from OHLCV (look-ahead safe)
  strategy/     # Strategy protocol + deterministic MomentumStrategy (v1)
  backtest/     # replay historical MarketState snapshots through a strategy
  broker/       # Broker protocol + FakeBroker (tests) + AlpacaBroker (paper/live)
  state/        # StateRepository protocol + InMemory/DynamoDB + reconcile rules
  engine.py     # TradingEngine: reconcile -> route -> decide -> conditional-write
tests/          # unit tests (pure, no AWS / broker / network)
examples/       # runnable demos
```

The strategy is a **pure function of `(MarketState, StrategyConfig)`** — no broker,
no AWS, no LLM. That contract is what keeps it unit-testable, backtestable, and
swappable, and lets the eventual LLM layer only *veto* a buy, never create one.

## Setup

Uses [uv](https://docs.astral.sh/uv/). Python ≥ 3.11.

```bash
uv sync                 # core deps (numpy, pandas) + dev tools
uv run pytest           # run the test suite
uv run ruff check .     # lint
uv run python examples/run_backtest.py   # synthetic end-to-end demo
```

Phase-specific deps are kept out of the default install:

```bash
uv sync --extra broker     # alpaca-py        (phase 3)
uv sync --extra aws        # boto3            (phase 4)
uv sync --extra reasoning  # langgraph        (phase 5)
```

## Secrets & config

Secrets are stored **locally as environment variables** or in **AWS SSM Parameter
Store** — never hardcoded. `StrategyConfig` loads from a dict (local JSON/YAML,
SSM JSON, or a DynamoDB item) so tuning is a zero-deploy change; every config and
strategy is versioned and stamped on each trade record.
