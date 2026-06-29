# Future improvements

The system is functionally complete — local development, backtesting, and paper/
live AWS deployment all work end-to-end. The items below are enhancements and
known gaps, grouped by area. None are blockers.

## Data tools (highest value)

The strategy decides from code-computed indicators today. Richer, timely context
would improve decision quality — and DECISIONS.md §8 notes that data quality, not
more agents, is where the leverage is. `AlpacaMarketDataProvider` computes
indicators from bars, while `context`, `news`, and `events` return empty/default.
Each is a drop-in `MarketDataProvider` implementation — **no engine/flow changes**:

- **News** — timestamped headlines (Alpaca news API / Polygon / Finnhub), filtered
  hard to recent/today (stale news is worse than none).
- **Economic calendar** — FOMC / CPI / jobs. This feeds the existing "no-trade
  window before a high-impact event" gate, which currently never fires because
  `events` is always empty.
- **Market context** — VIX / regime / premarket futures → `MarketContext`.
- **Data feed** — replace the free `iex` feed with `sip` once subscribed. The free
  feed reports only IEX volume (~2–3% of consolidated), so `min_relative_volume`
  and the ATR-quiet gate read systematically low and noisy.

## Strategy & risk refinements

- **ATR timeframe** — stops/takes are sized from *minute-bar* ATR, which is tiny;
  a `min_stop_loss_pct` floor compensates, but computing ATR on a longer timeframe
  (5-min / daily) would size stops from real intraday range at the source.
- **Fractional / notional orders** — whole-share sizing means a target below one
  share of an expensive instrument (e.g. QQQ ~$700) can't trade. Notional orders
  would allow small dollar positions (note: Alpaca brackets don't support
  fractional cleanly, so this needs a non-bracket entry path).

## Operational hardening

- **Promote to live** (DECISIONS.md §13 step 8) — after sustained clean paper runs,
  deploy the live stack at minimum share size. See
  [deployment.md](deployment.md#go-live-production).
- **Separate AWS account for live** (§3) — blast-radius isolation from paper.
- **`TRADING_ENABLED` kill-switch** — a handler env flag to pause trading
  independently of the EventBridge schedule (today you disable the rules or leave
  secrets unpopulated).
- **`USE_ADVISOR` as a deploy-time knob** — it's currently hardcoded on in the
  stack; exposing it (like `OPENROUTER_MODEL`) would allow an A/B of
  deterministic-only vs. advised on paper.

## Tooling / developer experience

- **`--mode paper|live` on the export scripts** — auto-resolve the table /
  log-group from the stack outputs instead of passing `--table` / `--log-group`.
- **Unify the export window** — `export_advisories.py` looks back by calendar day
  (UTC-boundary-sensitive); `export_runs.py` uses a rolling hour window. Make both
  rolling for consistency.

## Known minor issues

- The "too quiet" no-trade reason renders a stray double percent
  (`ATR%% below 0.100%`) — cosmetic, audit log only.
- The advisory evaluator's `reason` is often just `VETO`; `_parse_verdict` could
  capture its one-sentence rationale (the bull/bear/neutral branch theses already
  carry the real detail).
