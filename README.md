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

- ✅ **5. LangGraph reasoning** — a parallel data-gathering graph that assembles a
  `MarketState` (fan-out indicators/context/news/events → assemble), a
  Tree-of-Thought advisory subgraph (bull/bear/neutral → evaluator, biased to
  veto) over an injected `LLMClient`, and the **veto-only merge** wired into the
  engine (the LLM can downgrade a buy to do-nothing, never create one). Fakes
  make it all network-free; `OpenRouterLLM` is the real client.

- ✅ **6. Market-calendar gate + local runner** — a `MarketCalendar` gate (run
  does nothing when closed/holiday/half-day) wired as the engine's first step,
  and `run_once` (gather graph → engine) with a `build_local_engine` factory and
  `examples/run_local.py` CLI to run the whole flow against Alpaca paper +
  OpenRouter on your laptop. The full pipeline is tested end-to-end with fakes.

Not built yet (each layers on top without touching the above):

- ☐ 7. CDK deployment — paper + live stacks, EventBridge, Secrets Manager, alarms.
- ☐ Data tools — real news / economic-calendar / VIX providers (today
  `AlpacaMarketDataProvider` does bars→indicators; the rest return empty).

## Layout

```
src/trading_bot/
  domain/       # typed, pure data: MarketState, Decisions, StrategyConfig, Position
  indicators/   # code-computed signals from OHLCV (look-ahead safe)
  strategy/     # Strategy protocol + deterministic MomentumStrategy (v1)
  backtest/     # replay historical MarketState snapshots through a strategy
  broker/       # Broker protocol + FakeBroker (tests) + AlpacaBroker (paper/live)
  state/        # StateRepository protocol + InMemory/DynamoDB + reconcile rules
  reasoning/    # LangGraph: parallel gather -> MarketState, ToT advisory (veto-only)
  data/         # MarketDataProvider implementations (AlpacaMarketDataProvider)
  market_calendar.py  # is-the-market-open gate (Static + Alpaca)
  engine.py     # TradingEngine: gate -> reconcile -> route -> decide -> veto -> write
  runner.py     # run_once (gather -> engine) + build_local_engine factory
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

Phase-specific deps are kept out of the default install (combine as needed):

```bash
uv sync --extra broker     # alpaca-py  — Alpaca broker, data, calendar (phase 3, 6)
uv sync --extra aws        # boto3      — DynamoDB state (phase 4)
uv sync --extra reasoning  # langgraph + httpx — LangGraph + OpenRouter (phase 5)
```

## Environment variables & secrets

Secrets live **locally as environment variables** (and, when deployed, in **AWS
Secrets Manager / SSM** — never hardcoded). Nothing below is needed for the unit
tests or the synthetic backtest; they're only for runs that hit real services.

| Variable | Used by | Required when |
|---|---|---|
| `ALPACA_PAPER_API_KEY` / `ALPACA_PAPER_SECRET_KEY` | Alpaca paper broker, data, calendar | any real/paper run (falls back to `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`) |
| `ALPACA_LIVE_API_KEY` / `ALPACA_LIVE_SECRET_KEY` | live trading | live mode only (phase 7+) — **not** needed locally |
| `OPENROUTER_API_KEY` | OpenRouter advisory LLM | any run **with** the advisor (omit + use `--no-advisor` to skip) |
| `OPENROUTER_MODEL` | OpenRouter advisory LLM | optional; default `openai/gpt-4o-mini` |
| `DYNAMODB_TABLE` | runner state persistence | set to use DynamoDB instead of in-memory state |
| `DYNAMODB_ENDPOINT` | DynamoDB client | set to DynamoDB Local (e.g. `http://localhost:8000`); table auto-created |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | boto3 | required by boto3 even for DynamoDB Local — use dummy `local` / `local` |
| `AWS_DEFAULT_REGION` | boto3 | optional; default `us-east-1` |

Keys are **not interchangeable** between paper and live, and `mode` is explicit —
the bot refuses to start if the keys don't match the intended endpoint.
`StrategyConfig` loads from a dict (local JSON/YAML, SSM JSON, or a DynamoDB item),
so tuning is a zero-deploy change; every config and strategy is versioned and
stamped on each trade record.

## Run locally against paper (market open)

Dry-run the **real** flow on your laptop before deploying — e.g. on a Monday
morning, to confirm it will actually place a paper order. This runs the same
engine the Lambda will, against the Alpaca **paper** account, the LangGraph
data-gather + advisory, and **DynamoDB Local** for state.

```bash
# 1. install the extras for a real run
uv sync --extra broker --extra reasoning --extra aws

# 2. credentials (paper account + OpenRouter)
export ALPACA_PAPER_API_KEY=...      ALPACA_PAPER_SECRET_KEY=...
export OPENROUTER_API_KEY=...        # or pass --no-advisor and skip this

# 3. start DynamoDB Local and point the runner at it
make dynamo-up                        # docker compose up -d dynamodb
export DYNAMODB_ENDPOINT=http://localhost:8000
export DYNAMODB_TABLE=trading-bot-local
export AWS_ACCESS_KEY_ID=local  AWS_SECRET_ACCESS_KEY=local  AWS_DEFAULT_REGION=us-east-1

# 4. (optional) confirm connectivity to the paper endpoint first
uv run python examples/paper_smoke.py

# 5. run the bot — one shot, or on a loop through the session
uv run python examples/run_local.py                       # single run, right now
uv run python examples/run_local.py --loop --interval 60  # every 60s while open
```

What happens each run: the **market-calendar gate** checks Alpaca's calendar — if
the market is closed it does nothing. If open, it gathers bars → indicators,
makes the deterministic decision, lets the advisory **veto** (not create) a buy,
and — if it still decides to buy — places a **real bracket order** on your paper
account. State (the buy-once/sell-once gate + the run/trade audit records) is
written to DynamoDB Local. Because the strategy is biased toward `DO_NOTHING`, a
"no trade" result is normal and not a failure.

Inspect the persisted state/records with the optional admin UI:

```bash
docker compose --profile tools up -d   # dynamodb-admin at http://localhost:8001
```

> Skip DynamoDB entirely (in-memory state) by just not setting `DYNAMODB_TABLE` —
> useful for a quick smoke run, but state won't survive across restarts.

### Force an actual trade (validation only)

The strategy is biased toward `DO_NOTHING`, so a normal run often won't trade —
which makes it hard to confirm the *order* path works. `--force-entry` guarantees
a real paper order so you can watch the full **buy → state → sell** cycle:

```bash
uv run python examples/run_local.py --force-entry
```

It swaps in a dev-only `ForceEntryStrategy` (always buys the bullish instrument,
sized like the live rule but at least one share) and **disables the advisor** so
the LLM can't veto it. The market-calendar gate **still applies** — it only fires
a real order when the market is genuinely open, so run it during the session. The
trade is stamped `strategy_version="force-entry"` in the audit records so it's
obvious. After it fills, a normal exit (`MomentumStrategy`) manages and sells it,
or the EOD liquidation flattens it.

> ⚠️ `--force-entry` places a **real order on your paper account** and bypasses
> all conviction gates. It is for pre-deploy validation only — never use it for a
> deployed or live run.
