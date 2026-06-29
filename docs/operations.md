# Operating & inspecting

## Trade lifecycle (how selling works)

The Lambda is **stateless** and re-invoked on a schedule (~every 15 min on
weekdays); it does not sit running and watch a position. Each run reloads the
day's state from DynamoDB, reconciles against the broker, and decides. So selling
is just a *later* run finding the position open and choosing to exit:

```
09:45  run → NO_POSITION → BUY (bracket stop+take placed)   → POSITION_OPEN
10:00  run → OPEN → evaluate_exit → HOLD
10:15  run → OPEN → HOLD
 ...     (or the bracket stop/take fills on the broker between runs)
13:30  run → OPEN → momentum rolled over → SELL             → POSITION_CLOSED
13:45+ run → CLOSED → do nothing
```

A position is closed by any of three independent layers:

1. **Strategy exit** — on a scheduled run while open, `evaluate_exit` returns SELL
   on momentum rollover (`price < vwap` and `macd_hist < 0`) or at/after
   `force_exit_after` (15:55 ET); otherwise HOLD until the next run.
2. **Broker bracket** — the stop/take placed at entry fills **autonomously on the
   broker** between runs; the next run's `reconcile` marks the position closed.
3. **EOD MOC** — the ~15:47 ET run rests a market-on-close order that fills at the
   close even if no later run fires (see deployment.md → EOD safety).

## Inspecting the records

Two record stores answer different questions (DECISIONS.md §9), and there's a
script for each. Both need the `aws` extra and read-only AWS credentials:

```bash
uv sync --extra aws
```

## Run summaries (CloudWatch Logs) — *did it run, what did it decide?*

Every invocation logs a one-line JSON summary (`action`, `reason`,
`status_before/after`, `duration_ms`). `examples/export_runs.py` pulls those from
the Lambda's CloudWatch log group over a rolling window and writes a readable file
with a one-line-per-run table plus the full entries:

```bash
uv run --extra aws python examples/export_runs.py                 # last 2 days
uv run --extra aws python examples/export_runs.py --days 7 --out runs.txt
```

Flags: `--days` (look-back window), `--out`, `--log-group`, `--region`. A normal
day is mostly `DO_NOTHING` (calendar-gated off-hours, or no conviction) — that's
correct, not a failure.

## Advisory reasoning (DynamoDB) — *why did the LLM veto?*

The CloudWatch summary doesn't include the LLM's reasoning — the **full
Tree-of-Thought blob** (bull/bear/neutral theses, the evaluator's verdict, and
`llm_calls`) is persisted to the **DynamoDB run records**.
`examples/export_advisories.py` reads them and prints, for each run that invoked
the advisor, the recommendation, the branch arguments, and `llm_calls`:

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

> Both scripts default to **the paper deployment's** auto-generated log-group /
> table names. If you redeploy into a fresh stack — or want the live stack — they
> change; find the current names with
> `aws cloudformation describe-stacks --stack-name TradingBot<Paper|Live>` (the
> `TableName` output and the Lambda's log group) and pass `--table` / `--log-group`.

## Verifying the advisory model

Before relying on a model in a deploy, confirm it works end-to-end (a ping plus a
full ToT advisory on a synthetic buy):

```bash
export OPENROUTER_API_KEY=sk-or-...  OPENROUTER_MODEL=anthropic/claude-3.5-haiku
uv run --extra reasoning python examples/check_model.py
```

`llm_calls > 0` in the output means the model is reachable and the advisory path
runs. The same `OPENROUTER_MODEL` value is what you `export` before deploying.

## Underlying data (raw)

- **CloudWatch Logs** — operational logs (errors, timings, the run summaries).
  Query ad-hoc in the console with Logs Insights.
- **DynamoDB** — the durable system-of-record, single table keyed
  `PK=DATE#<date>` with `SK` of `STATE` (the daily gate), `RUN#<ts>` (one per run,
  with the advisory blob), and `TRADE#<order_id>` (one per actual order). A GSI
  (`GSI1`) indexes trades by `strategy_version` for cross-day analysis. Paper and
  live each have their **own** table.
