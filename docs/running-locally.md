# Running locally

Most development is local. The same engine the Lambda runs can be driven from
your laptop against the Alpaca **paper** endpoint, OpenRouter, and either an
in-memory store or DynamoDB Local — the only difference vs. AWS is where secrets
and config load from (DECISIONS.md §11).

## Setup & dependency extras

Uses [uv](https://docs.astral.sh/uv/). Python ≥ 3.11.

```bash
uv sync                 # core deps (numpy, pandas) + dev tools
uv run pytest           # the test suite (pure, no network)
uv run ruff check .     # lint
uv run python examples/run_backtest.py   # synthetic end-to-end demo
```

Phase-specific deps are kept out of the default install (combine as needed):

```bash
uv sync --extra broker     # alpaca-py  — Alpaca broker, data, calendar
uv sync --extra aws        # boto3      — DynamoDB / SSM
uv sync --extra reasoning  # langgraph + httpx — LangGraph + OpenRouter
uv sync --extra infra      # aws-cdk-lib — CDK app (deploy only)
```

## Environment variables & secrets

Secrets live **locally as environment variables** (and, when deployed, in **SSM
SecureStrings** — never hardcoded). Nothing below is needed for the unit tests or
the synthetic backtest; they're only for runs that hit real services.

| Variable | Used by | Required when |
|---|---|---|
| `ALPACA_PAPER_API_KEY` / `ALPACA_PAPER_SECRET_KEY` | Alpaca paper broker, data, calendar | any real/paper run (falls back to `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`) |
| `ALPACA_LIVE_API_KEY` / `ALPACA_LIVE_SECRET_KEY` | live trading | live mode only — **not** needed locally |
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

## Strategy config & decision parity

`StrategyConfig` holds every tunable (sizing, RSI bands, volume/ATR gates, entry/
exit times, the MOC window, the instrument set). The **same resolver**
(`load_strategy_config`) feeds both local and deployed runs, so the only thing
that makes a local decision differ from prod is the config it reads. Point local
at the config you deploy for decision-parity:

```bash
# local: a JSON/YAML file (a starter lives at examples/strategy_config.example.json)
uv run python examples/run_local.py --config examples/strategy_config.example.json
export STRATEGY_CONFIG_FILE=examples/strategy_config.example.json   # or via env

# deployed: the same shape, stored in SSM (read by the Lambda)
export STRATEGY_CONFIG_SSM=/trading-bot/paper/strategy-config
```

Precedence: explicit `--config` → `STRATEGY_CONFIG_FILE` → `STRATEGY_CONFIG_SSM` →
built-in defaults. The run banner prints which source was used (`config=...`).
Tuning is a zero-deploy change; every config and strategy is versioned and
stamped on each trade record.

## Run against Alpaca paper (market open)

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

Each run: the **market-calendar gate** checks Alpaca's calendar — if the market
is closed it does nothing. If open, it gathers bars → indicators, makes the
deterministic decision, lets the advisory **veto** (not create) a buy, and — if it
still decides to buy — places a **real bracket order** on your paper account.
State (the buy-once/sell-once gate + the run/trade audit records) is written to
DynamoDB Local. Because the strategy is biased toward `DO_NOTHING`, a "no trade"
result is normal and not a failure.

Inspect the persisted state/records with the optional admin UI:

```bash
docker compose --profile tools up -d   # dynamodb-admin at http://localhost:8001
```

> Skip DynamoDB entirely (in-memory state) by just not setting `DYNAMODB_TABLE` —
> useful for a quick smoke run, but state won't survive across restarts.

## Force an actual trade (validation only)

The strategy is biased toward `DO_NOTHING`, so a normal run often won't trade —
which makes it hard to confirm the *order* path works. `--force-entry` guarantees
a real paper order so you can watch the full **buy → state → sell** cycle:

```bash
uv run python examples/run_local.py --force-entry
```

It swaps in a dev-only `ForceEntryStrategy` (always buys the bullish instrument,
sized like the live rule but at least one share, with a deliberately **wide**
bracket so it isn't stopped out instantly) and **disables the advisor** so the LLM
can't veto it. The market-calendar gate **still applies** — it only fires a real
order when the market is genuinely open, so run it during the session. The trade
is stamped `strategy_version="force-entry"` in the audit records.

```bash
# extra knobs for force-entry validation
--loop --interval 30     # keep running to watch buy -> manage -> sell
--stop-pct 0.002         # tighten the bracket to test a stop/take fill fast
--cycle N                # DEV: run an extra cycle on a synthetic trade-date today
```

> ⚠️ `--force-entry` places a **real order on your paper account** and bypasses
> all conviction gates. It is for pre-deploy validation only — never use it for a
> deployed or live run.
