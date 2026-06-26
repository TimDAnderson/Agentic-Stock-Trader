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

- ✅ **7. CDK deployment** — a parameterized `TradingBotStack` (paper = staging,
  live = prod) provisioning the DynamoDB table (schema matches the repo), an SSM
  `StrategyConfig` parameter, IAM grants to read the **SSM SecureString** secrets
  you create out-of-band, a container-image Lambda running
  `trading_bot.aws.handler.handler`, EventBridge schedules (weekday/session
  cadence + a guaranteed near-close liquidation fire, both made correct by the
  in-handler calendar gate), and CloudWatch alarms → SNS. See
  **[Deploy to AWS](#deploy-to-aws-phase-7)**.

Not built yet (each layers on top without touching the above):

- ☐ 8. Promote — sustained paper runs → live at minimum share size.
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
| `STRATEGY_CONFIG_FILE` | `StrategyConfig` loader | optional; path to a JSON/YAML config file (else defaults) |
| `STRATEGY_CONFIG_SSM` | `StrategyConfig` loader | optional; SSM parameter name holding a JSON config (needs `aws` extra) |
| `DYNAMODB_TABLE` | runner state persistence | set to use DynamoDB instead of in-memory state |
| `DYNAMODB_ENDPOINT` | DynamoDB client | set to DynamoDB Local (e.g. `http://localhost:8000`); table auto-created |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | boto3 | required by boto3 even for DynamoDB Local — use dummy `local` / `local` |
| `AWS_DEFAULT_REGION` | boto3 | optional; default `us-east-1` |

Keys are **not interchangeable** between paper and live, and `mode` is explicit —
the bot refuses to start if the keys don't match the intended endpoint.
`StrategyConfig` loads from a dict (local JSON/YAML, SSM JSON, or a DynamoDB item),
so tuning is a zero-deploy change; every config and strategy is versioned and
stamped on each trade record.

The **same resolver** (`load_strategy_config`) feeds both local and deployed runs,
so the only thing that makes a local decision differ from prod is the config it
reads. Point local at the config you deploy for decision-parity:

```bash
# local: a JSON/YAML file (a starter lives at examples/strategy_config.example.json)
uv run python examples/run_local.py --config examples/strategy_config.example.json
export STRATEGY_CONFIG_FILE=examples/strategy_config.example.json   # or via env

# deployed: the same shape, stored in SSM (read by the Lambda)
export STRATEGY_CONFIG_SSM=/trading-bot/strategy-config
```

Precedence: explicit `--config` → `STRATEGY_CONFIG_FILE` → `STRATEGY_CONFIG_SSM` →
built-in defaults. The run banner prints which source was used (`config=...`).

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

## Deploy to AWS (phase 7)

The **paper stack is your staging environment** (§10): same handler the live
stack runs, real plumbing/timing, fake money. The Lambda runs the *same*
`build_engine` wiring as the local runner — only the secret/config *source*
differs (§11).

**Prerequisites**

```bash
npm install -g aws-cdk          # the CDK CLI (Toolkit)
uv sync --extra infra           # aws-cdk-lib for the Python app
aws configure                   # credentials for the target (staging) account
cdk bootstrap                   # once per account/region
```

**Deploy the staging (paper) stack**

```bash
make deploy-paper               # = cd infra && cdk deploy TradingBotPaper
```

This builds the container image, creates the DynamoDB table, the SSM config
parameter (from `examples/strategy_config.example.json` — or point
`STRATEGY_CONFIG_FILE` at your tuned file), the EventBridge schedules, and alarms.
It does **not** create the secrets (see below). The stack prints a
`SecretSsmParams` output listing the SecureString parameter names the Lambda
reads.

### Secrets: SSM SecureStrings you create yourself

Secrets are **SSM Parameter Store `SecureString` parameters** — you create and
rotate them out-of-band (never in code/CloudFormation). CDK only grants the
Lambda permission to **read + KMS-decrypt** them. Create these three under
`/trading-bot/paper/` (the live stack uses `ALPACA_LIVE_*` under
`/trading-bot/live/`):

```bash
aws ssm put-parameter --type SecureString --name /trading-bot/paper/ALPACA_PAPER_API_KEY    --value 'PK...'
aws ssm put-parameter --type SecureString --name /trading-bot/paper/ALPACA_PAPER_SECRET_KEY --value '...'
aws ssm put-parameter --type SecureString --name /trading-bot/paper/OPENROUTER_API_KEY      --value 'sk-or-...'
```

The **parameter's last path segment is the env-var name the code reads**
(`/trading-bot/paper/ALPACA_PAPER_API_KEY` → `ALPACA_PAPER_API_KEY`). The handler
loads them into the environment at cold start, so `load_credentials` /
`OpenRouterLLM` find their keys exactly as they do locally. Until they exist the
run fails loudly (safe) rather than trading blind. Rotating = another
`put-parameter --overwrite`; **no redeploy** (applies on the next cold start).

> Using the default `aws/ssm` KMS key needs no extra setup — the stack grants
> `kms:Decrypt` scoped to the SSM service. A **customer-managed** KMS key would
> also need that key's policy to allow the Lambda role.

The non-secret **`StrategyConfig` is created by CDK** (plain SSM `String`) and is
a zero-deploy tuning lever — edit the parameter to retune; the handler reads it
each run.

**Verify it's running:** invoke once by hand, then watch the logs / DynamoDB.

```bash
aws lambda invoke --function-name <FunctionName-from-outputs> /dev/stdout
```

A `DO_NOTHING` result (or "Market closed (calendar gate)" off-hours) is the
normal, correct outcome — the schedule fires often; the gate + strategy keep most
runs idle.

> The live stack is a second instantiation (`make deploy-live`, separate account
> recommended). Don't promote until paper has run cleanly for a sustained period
> (§10), and go live at **minimum share size** (§14).

### EOD safety: two independent backstops

Flat-by-close is enforced two ways, so a single failure can't strand a position:

1. **Resting MOC (market-on-close)** — at ~15:47 ET (the `MocBackstop` rule) the
   engine cancels the bracket legs and places a **market-on-close sell** that the
   exchange fills in the closing auction **even if no later Lambda runs**. This is
   the "survives a dead Lambda" guarantee. Window/toggle: `place_moc_after`,
   `moc_cutoff`, `moc_backstop_enabled` in `StrategyConfig`.
2. **Scheduled liquidation** — at ~15:56 ET (the `EodLiquidation` rule) the
   strategy's `force_exit_after` triggers `close_all_positions`, an immediate
   market exit that also cancels the resting MOC. If runs keep firing this is what
   flattens you; the MOC is the insurance for when they don't.

Trade-off (by design): between the MOC placement (~15:47) and the close there is
no hard stop — the bracket was cancelled so the MOC can't be left selling shares a
stop already closed (which would open a short). It's a ~10-minute window on a
buy-once/flat-by-close bot.

To **watch it on paper:** force an entry mid-session and leave the loop running
into the afternoon, or invoke the deployed Lambda after 15:45 ET — a run logs
`action: MOC` once, then the position flattens at the close.

## Inspecting runs & decisions

There are **two** record stores (DECISIONS.md §9), and a script for each. Both
need the `aws` extra and read-only AWS credentials:

```bash
uv sync --extra aws
```

### Run summaries (CloudWatch Logs) — *did it run, what did it decide?*

Every invocation logs a one-line JSON summary (`action`, `reason`,
`status_before/after`, `duration_ms`). `examples/export_runs.py` pulls those from
the Lambda's CloudWatch log group over a rolling window and writes a readable file
with a one-line-per-run table plus the full entries:

```bash
uv run --extra aws python examples/export_runs.py                 # last 2 days
uv run --extra aws python examples/export_runs.py --days 7 --out runs.txt
```

Flags: `--days` (look-back window), `--out` (output file), `--log-group`,
`--region`. A normal day is mostly `DO_NOTHING` (calendar-gated off-hours, or no
conviction) — that's correct, not a failure.

### Advisory reasoning (DynamoDB) — *why did the LLM veto?*

The CloudWatch summary doesn't include the LLM's reasoning — the **full
Tree-of-Thought blob** (bull/bear/neutral theses, the evaluator's verdict, and
`llm_calls`) is persisted to the **DynamoDB run records**. `examples/export_advisories.py`
reads them and prints, for each run that invoked the advisor, the recommendation,
the branch arguments, and — crucially — `llm_calls`:

```bash
uv run --extra aws python examples/export_advisories.py --days 3 --out vetoes.txt
```

Flags: `--days`, `--table`, `--region`, `--out`. The **`llm_calls`** field tells
you which kind of veto it is:

- **`llm_calls > 0`** (≈4: bull/bear/neutral + evaluator) → genuine reasoning;
  read the branch theses to see *why*.
- **`llm_calls == 0`** → the advisor **errored and safe-defaulted to VETO** (bad
  model slug, missing/blocked `OPENROUTER_API_KEY`, timeout). The `reason` reads
  `Advisory failed (...)`. This is a config problem masquerading as caution — it
  would silently veto every buy.

> Both scripts default to **this deployment's** auto-generated log-group / table
> names. If you redeploy into a fresh stack they change — find the current ones
> with `aws cloudformation describe-stacks --stack-name TradingBotPaper` (the
> `TableName` output and the Lambda's log group) and pass `--table` / `--log-group`.

### Underlying data (raw)

- **CloudWatch Logs** — operational logs (errors, timings, the run summaries).
  Query ad-hoc in the console with Logs Insights.
- **DynamoDB** — the durable system-of-record, single table keyed
  `PK=DATE#<date>` with `SK` of `STATE` (the daily gate), `RUN#<ts>` (one per run,
  with the advisory blob), and `TRADE#<order_id>` (one per actual order). A GSI
  (`GSI1`) indexes trades by `strategy_version` for cross-day analysis.
