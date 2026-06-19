# Trading Bot — Project Decisions & Handoff

> Context document for building an intraday QQQ/PSQ trading bot. Paste this into
> Claude Code (or keep it committed at the repo root) so development starts with
> full context. Everything below was decided during planning.

---

## 1. Goal & Strategy Shape

- Intraday strategy: each morning, decide to bet the market **up** (`BUY_BULLISH`), **down** (`BUY_BEARISH`), or **do nothing**. The decision is a *direction*; the actual ticker bought is resolved from config.
- **Instruments are configurable** (`InstrumentConfig`), not hardcoded: a `reference_symbol` the directional view is read from (normally QQQ), plus the `bullish_symbol`/`bearish_symbol` actually traded. Two presets: **QQQ/PSQ** (default, unleveraged) and **TQQQ/SQQQ** (3× leveraged — view still read from QQQ). The bot always goes *long* the chosen ETF (buys the inverse rather than shorting), so every position is long.
- **Buy once and sell once per day.** The bot runs **many times per day**, but only one entry and one exit may occur.
- Position must be **flat by market close** every day.
- "Do nothing" is a first-class, frequently-chosen outcome. Bias toward declining — a missed entry is cheap; a bad entry is expensive.

---

## 2. Broker — Alpaca

- Broker is **Alpaca**. Trading is commission-free for equities.
- Two modes: **paper** and **live**.
  - Paper endpoint: `https://paper-api.alpaca.markets`
  - Live endpoint:  `https://api.alpaca.markets`
  - Keys are **not interchangeable** between modes — a wrong key fails loudly.
- `mode` is a **required, explicit** parameter. No default. Code must refuse to start if `mode` is unset or invalid (never fall through to live).
- `alpaca-py` `TradingClient(key, secret, paper=(mode == "paper"))` selects the endpoint.
- Verify account identity at startup (call the account endpoint, assert it matches the expected mode) before placing any order.

---

## 3. Architecture

- **Compute:** AWS **Lambda + EventBridge** (scheduled cron triggers). Default choice — workload is bursty/scheduled, scales to zero, cheap.
- **Scheduling & market-calendar gate:** EventBridge cron is a *coarse* trigger (weekdays only, during the ET session window — expressed in UTC, mind DST). It does **not** know holidays or half-days, so **every run first checks the market calendar** (Alpaca's calendar API) and short-circuits to *do nothing* if the market is closed or it's outside session hours. This is how "only runs on days the market is open" is actually enforced: the schedule narrows the firings, the runtime gate makes it correct (holidays, early closes, DST). On half-days the gate shifts the entry-cutoff / EOD-liquidation times to the real close. The gate is shared code, so it also protects the local runner (§11).
- **State:** DynamoDB. **Secrets:** AWS Secrets Manager (never env vars / hardcoded). **Config:** SSM Parameter Store or DynamoDB. **Logs/alarms:** CloudWatch.
- **IaC:** AWS **CDK in Python** (same language as the bot). Define the stack as a parameterized class, instantiate **two stacks** (paper, live), ideally in **separate AWS accounts** for blast-radius isolation.
- Package LangChain/LangGraph deps as a **container-image Lambda** (zip size limit).
- **Hybrid fallback:** if the afternoon "find the right time to sell" logic needs continuous polling over hours, or runs exceed ~15 min, move that piece to a **Fargate scheduled task** (no 15-min limit, same container). Don't build this until measured to be needed.
- **Avoid the NAT Gateway trap** (~$35+/mo flat). Don't put Lambda in a private VPC subnet unless required; use public subnet + security groups or VPC endpoints.

---

## 4. Buy-Once / Sell-Once State Machine

```
NO_POSITION --(buy)--> POSITION_OPEN --(sell)--> POSITION_CLOSED  (terminal for the day)
```

Every run: **market-open gate → reconcile → route by state → decide → conditional-write**.

- **Market-open gate first.** Before any broker or DB work, confirm the market is open *right now* (calendar API + session window). Closed / holiday / outside-hours → record a `do nothing` run and exit immediately. Cheap, and it renders any weekend/holiday EventBridge misfire harmless. Same gate runs locally and in AWS.
- **DynamoDB conditional writes** gate transitions (e.g. only `NO_POSITION -> POSITION_OPEN` if status is still `NO_POSITION`). Prevents double-buys from overlapping runs / retries.
- **Idempotent `client_order_id`** keyed to the date (e.g. `2026-06-02-ENTRY`) — broker rejects duplicates. Defense in depth with the conditional write.
- **Broker is source of truth.** Each run calls `get_positions()` first and reconciles. If DB says `POSITION_OPEN` but broker has no position, the stop/EOD already fired → transition to `POSITION_CLOSED`, do not re-sell. On disagreement, safe default is "do nothing + alert".
- Write intent (with the idempotent ID) **before** the order; reconcile against the broker after. Never "order then write state" with a gap that a crash could exploit.
- State keyed by `trade_date` → natural daily reset.
- Define an **entry cutoff** (e.g. no new entries after 14:30 ET) so late runs can only manage/sell, never open.

---

## 5. Orders — Stop-Loss + End-of-Day Liquidation

Two different mechanisms combined (a bracket stop alone does NOT guarantee EOD exit):

1. **Entry = bracket order** (`OrderClass.BRACKET`): entry + take-profit + stop-loss as an OCO pair. Gives every position an automatic stop on fill. Stop sized from ATR via config.
2. **EOD exit = defense in depth:**
   - **Scheduled liquidation run** near close (~15:55 ET): `close_all_positions(cancel_orders=True)`.
   - **Broker-side backstop** that survives a failed Lambda: an Alpaca **MOC (market-on-close)** sell placed in the afternoon (has a pre-close submission cutoff).
   - **CloudWatch alarm** if a position is still open after close.
- `time_in_force=DAY` only cancels *unfilled* orders — it does NOT close a filled position. Hence the explicit liquidation.
- All order types (bracket, stop-loss/take-profit legs, `close_all_positions`, MOC) **work in paper mode** — only the endpoint/keys differ.

> **Caveat:** Paper fills are optimistic — stop-losses fill cleanly in paper but can gap well below the stop live (a stop becomes a market order when triggered; no guaranteed fill price). Paper validates *mechanics*, not live fill behavior. Go live at minimum share size first.

---

## 6. Code Contracts

Separate three concerns: **plumbing** (rarely changes), **strategy** (changes constantly), **reasoning/prompts** (changes occasionally). Strategy sits behind a clean interface so it is swappable without touching plumbing.

```python
class Strategy(Protocol):
    version: str  # stamped on every trade record for attribution & rollback
    def evaluate_entry(self, state: MarketState, config: StrategyConfig) -> EntryDecision: ...
    def evaluate_exit(self, state: MarketState, position: Position, config: StrategyConfig) -> ExitDecision: ...
```

- **`MarketState`** is the single input to a decision — a snapshot with an `as_of` timestamp. **A strategy may only use data at or before `as_of`.** This structurally prevents look-ahead bias and makes backtesting = replaying historical `MarketState` snapshots.
  - Holds **code-computed** `Indicators`, keyed by symbol, for the reference index and each tradable instrument (price, vwap, sma, ema, rsi, macd, atr, relative_volume, gap_pct), the account `equity` at `as_of` (so sizing stays a pure function), `MarketContext` (vix, regime, premarket futures), timestamped `NewsItem`s, and `EconomicEvent`s. **No raw OHLCV, no live broker calls.**
- **Decisions** are typed: `EntryDecision(action ∈ {BUY_BULLISH, BUY_BEARISH, DO_NOTHING}, qty, stop_loss_price, take_profit_price, reason)` and `ExitDecision(action ∈ {SELL, HOLD}, reason)`. The action is a direction; the ticker is resolved from `InstrumentConfig`. Every decision carries a `reason` for the audit log.
- **`StrategyConfig`** holds all tunables (rsi thresholds, min relative volume, no_entry_after, stop_loss_atr_multiple, no-trade window before events, the nested `instruments` set, and **position sizing** — `target_position_usd` for a fixed ~dollar amount, else `max_position_pct` of equity; the dollar amount wins when set). Loaded from SSM/DynamoDB → tuning is a **zero-deploy** config change.
- `evaluate_entry`/`evaluate_exit` are **pure functions of (state, config)** → fully unit-testable and backtestable with no AWS / LLM / broker.

---

## 7. Reasoning Layer — LangGraph (single framework)

- Use **LangGraph** as the **only** orchestration framework. **Do NOT add CrewAI** — it overlaps LangGraph, pulls toward autonomous delegation (wrong for money), and increases LLM calls/latency/non-determinism. CrewAI only makes sense for a decoupled, non-time-critical research job later (e.g. an overnight briefing).
- **Run graph** mirrors the state machine: `reconcile` node → conditional edges route to entry-gathering, exit-gathering, or done.
- **Parallel data gathering:** fan out to concurrent nodes (indicators / context / news / events) → fan in to `assemble` the `MarketState`. Bounded by the slowest fetch, not the sum.
- **Decision node — the core principle:** the **deterministic `Strategy.evaluate_entry` decides**. An LLM/ToT advisory pass may **veto/downgrade a buy to do-nothing, but can NEVER create a buy**. The deterministic, testable, versioned rule holds the wheel.
- **Tree of Thought = advisory subgraph only:** parallel bull/bear/neutral branches → evaluator → recommendation (`proceed` | `veto`). Structures reasoning; does NOT add predictive accuracy. Bias evaluator toward veto.
- **Bound everything** (protects both LLM cost and the 15-min limit): per-LLM-call timeouts (~20–30s), low max agent steps (~6–8), LangGraph recursion/step limit set low, limited retries. If the graph times out, the Lambda defaults to **do nothing** (safe).
- Persist the **full final graph state** each run to DynamoDB (the audit record).

---

## 8. Data Tools

- **Code computes signals; the LLM only interprets them.** Never ask the LLM to do arithmetic or "detect patterns" from raw numbers. Compute RSI/VWAP/MACD/ATR/crossovers/gaps in code (`pandas-ta` / `TA-Lib`); hand the agent clean values/booleans.
- Tools: market data + indicators (Alpaca), broader context (VIX, regime, premarket — Alpaca/other), news (Alpaca news API / Polygon / Finnhub — timestamped), **economic calendar** (FOMC/CPI/jobs — high value, often the biggest intraday mover).
- **Freshness is everything** — every tool returns timestamps; filter hard to recent/today. Stale news is worse than none.
- **Garbage in, garbage out** dominates — most engineering effort goes into clean, timely data tools, not more agents.
- **Conflicting signals need an explicit resolution rule** (e.g. "any high-impact event within N hours → do nothing"). Don't let an LLM arbitrate inconsistently.
- Avoid general web search for time-sensitive decisions (slow, noisy, stale-prone).

---

## 9. Persistence & Logs

Two separate systems:

- **CloudWatch = operational logs** (did it run, errors, timings). Log **structured JSON**; query with Logs Insights. Set retention (30–90 days). **Alarms** for: run errors, position open near close, `duration_ms` over threshold, broker/DB mismatch.
- **DynamoDB = durable decision/trade system-of-record** (queryable, never expires). Single-table design:
  - `PK="DATE#<date>" SK="STATE"` — mutable daily state (the gate).
  - `PK="DATE#<date>" SK="RUN#<ts>"` — **append-only** decision per run: action, reason, `market_snapshot`, `advisory`, `strategy_version`, `mode`, `duration_ms`, `llm_calls`. Captures the many "do nothing" runs too.
  - `PK="DATE#<date>" SK="TRADE#<order_id>"` — append-only per actual order: fills, stop, pnl, `strategy_version`, `mode`, timestamps.
- **Every record stamped with `strategy_version` and `mode`** — enables version attribution/rollback and clean paper/live separation.
- Large blobs (full ToT text) → store trimmed summary in DynamoDB, verbose blob in **S3** with a pointer key.
- **Cross-day queries** (e.g. "all trades for strategy v1") need a **GSI** keyed on `strategy_version` (or `mode#strategy_version`). For aggregate analysis, pull records into pandas.

---

## 10. Strategy Iteration

- 90% of "updates" are **config tuning** → live in SSM/DynamoDB → zero-deploy.
- **Version** every strategy + config; stamp version on every trade. Keep config in git (history + review), not edited live in console.
- **Backtest harness** with strict look-ahead protection (strategy sees only `state.as_of` and earlier).
- **Promotion path** (same discipline as code): edit + unit-test + backtest locally → deploy to **paper stack** → run on live market with fake money for a period → review logged trades vs. prior version → promote same version to **live stack**. Never edit a live strategy directly. **Paper stack = strategy staging.**
- Version prompts too if reasoning lives in prompts.

---

## 11. Testing — Local First

Most development is **local**; AWS is only for plumbing/timing validation.

- **Fully local (no AWS):** strategy unit tests, backtest harness, indicator computation. Pure functions.
- **Local against real services:** Alpaca **paper endpoint** is just HTTPS — hit it from your laptop (real bracket orders, idempotent IDs, liquidation). LangGraph runs locally (measure run duration + LLM-call count vs. the 15-min limit here). OpenRouter is just an API — stub/mock it during fast iteration to save tokens.
- **Local emulation:** DynamoDB via **DynamoDB Local** or **LocalStack**; Lambda handler via **SAM** (`sam local invoke`) with emulated EventBridge/DynamoDB/Secrets Manager.
- **Local end-to-end run (laptop, real services) — required before any deploy.** A single local entrypoint runs the **same handler logic** as Lambda against **Alpaca paper + OpenRouter (real) + local state** (DynamoDB Local, or the in-memory repo for the quickest loop). Runnable one-shot or on a local schedule/loop. This proves the *whole* flow — gate → data → reasoning → decision → order → state record — works end-to-end before AWS is ever touched. The market-calendar gate runs here too, so a local run on a closed day correctly does nothing. Credentials come from local env vars (`ALPACA_*`, `OPENROUTER_*`); the only difference vs. AWS is where secrets/config load from.
- **Only validated when deployed:** IAM permissions, real cold-start/EventBridge timing, real Secrets Manager, VPC networking.
- Use **config / dependency injection** to toggle local vs. deployed endpoints — the broker, repository, secrets, and clock are all injected, so "local" vs "AWS" is wiring, not new logic.

Environments: **local** (dev/unit/backtest) → **local end-to-end** (Alpaca paper + OpenRouter + DynamoDB Local, run from your laptop) → **staging = paper AWS stack** (real plumbing/timing, fake money) → **prod = live AWS stack** (min share size first).

> Local/paper tests prove the system **works** (orders submit, state transitions, under time budget). They do **NOT** prove **edge/profitability** — paper fills are optimistic, backtests can flatter. Keep "does it work?" and "does it make money?" separate.

---

## 12. Costs (rough, monthly)

- **AWS is cheap:** Lambda path ~$5–$20/mo all-in (free tiers help); ~double for both paper+live stacks. **Watch the NAT Gateway (~$35+/mo).** Fargate path ~$15–$40/mo.
- **LLM calls (OpenRouter) = likely the biggest variable cost** — scales with branch count + context size + model tier (single-digit dollars on a cheap model to hundreds on a frontier model with big context). Main cost lever → keep reasoning lean, cap calls, cheap model for research / smart model only for final decision.
- **Data/news APIs** can exceed the AWS bill ($0–$200+/mo per provider depending on real-time tiers).
- Measure real OpenRouter spend during paper trading; dial model/branch count against actual numbers.

---

## 13. Suggested Build Order

1. **`Strategy` interface + `MarketState` + `StrategyConfig`** — decouple strategy from plumbing from day one. Add the deterministic rule + unit tests.
2. **Indicator/pattern computation** (code-computed signals) + the backtest harness with look-ahead protection.
3. **Alpaca paper flow** — bracket order with stop-loss, idempotent client_order_id, `close_all_positions`, MOC backstop. Test against the real paper endpoint locally.
4. **Persistence** — DynamoDB state machine (conditional writes), append-only decision/trade records, broker reconciliation. Test against DynamoDB Local.
5. **LangGraph layer** — parallel gathering + ToT advisory (veto-only), with timeouts/step caps. Measure run time + LLM calls.
6. **Market-calendar gate + local end-to-end runner** — a calendar-aware "is the market open now?" gate shared by every entrypoint, plus a laptop CLI that runs the whole flow against **Alpaca paper + OpenRouter + local state** (one-shot and on a loop). **Validate the full flow locally before any deploy** — this is the go/no-go for AWS.
7. **CDK deployment** — parameterized stack, paper + live (separate accounts), EventBridge schedules (weekday/session cron, with the calendar gate enforced in-handler), Secrets Manager, alarms.
8. **Promote** — sustained paper runs → live at minimum size.

---

## 14. Standing Caveats

- **Not financial advice.** An automated bot trading real money carries real loss risk.
- **More agents / fancier reasoning ≠ more predictive power** on short-term price. ToT/multi-agent improve reasoning *structure* and can breed false confidence around what is essentially a coin flip intraday. The **deterministic, backtested rule** is the only part that can be trusted to production — LLM reasoning advises and can only make the system *more* cautious.
- **Paper results are not proof of edge.** Optimistic fills; live differs (slippage, gaps, partial fills).
- **PDT rule:** repeated same-day round trips under $25k equity can trigger Pattern Day Trader restrictions — check before going live.
- **Inverse/leveraged ETFs decay** — PSQ (inverse) and especially TQQQ/SQQQ (3× leveraged) suffer volatility decay, making them intraday-only; fine here, but understand the instrument. **Leverage cuts both ways:** the TQQQ/SQQQ preset swings ~3× and can lose value far faster than QQQ/PSQ — ATR-based stops scale with that, but size accordingly and start small live.
- Mind **timezones (EventBridge is UTC, markets are ET, DST shifts)** and **market holidays / half-days** — these are handled by the first-class **market-calendar gate** (§3, §4) shared by the local runner and Lambda, not by cron alone. Cron only narrows *when* it fires; the gate decides whether the market is actually open.
