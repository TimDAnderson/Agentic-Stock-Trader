"""Run the full bot locally against Alpaca paper + OpenRouter (DECISIONS.md §13 step 6).

The laptop go/no-go before AWS: the same engine the Lambda will run, wired to the
real paper broker, the market-calendar gate, the LangGraph data-gather + ToT
advisory, and local state.

Setup:
    uv sync --extra broker --extra reasoning
    export ALPACA_PAPER_API_KEY=...   ALPACA_PAPER_SECRET_KEY=...
    export OPENROUTER_API_KEY=...     # omit and pass --no-advisor to skip the LLM

Usage:
    uv run python examples/run_local.py                 # one run, now
    uv run python examples/run_local.py --no-advisor    # deterministic only
    uv run python examples/run_local.py --loop --interval 300   # every 5 min
    uv run python examples/run_local.py --force-entry --cycle 1  # extra test cycle

The calendar gate means a run outside market hours correctly does nothing.
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from trading_bot.config_loader import describe_source
from trading_bot.runner import build_local_engine, run_once

ET = ZoneInfo('America/New_York')


def main() -> None:
    parser = argparse.ArgumentParser(description='Local end-to-end paper run.')
    parser.add_argument('--no-advisor', action='store_true', help='skip the LLM advisory pass')
    parser.add_argument(
        '--force-entry',
        action='store_true',
        help='DEV ONLY: force a bullish buy (no advisor) to validate the buy->sell path',
    )
    parser.add_argument('--loop', action='store_true', help='run repeatedly')
    parser.add_argument(
        '--interval', type=float, default=300.0, help='seconds between runs (--loop)'
    )
    parser.add_argument(
        '--cycle',
        type=int,
        default=0,
        help=(
            'DEV ONLY: run an extra buy-once/sell-once cycle on a synthetic '
            'trade-date = today+N (unique state key + client_order_id). Bump it '
            'each session to re-test the full path on the same real day.'
        ),
    )
    parser.add_argument(
        '--stop-pct',
        type=float,
        default=0.05,
        help=(
            'force-entry only: protective bracket width as a fraction of price '
            '(default 0.05 = 5%%). Wide so the position survives to watch the '
            'exit logic; lower it to test a stop/take fill.'
        ),
    )
    parser.add_argument(
        '--config',
        default=None,
        help=(
            'path to a JSON/YAML StrategyConfig file (else STRATEGY_CONFIG_FILE / '
            'STRATEGY_CONFIG_SSM env, else defaults). Point at the deployed config '
            'to make local decisions match prod.'
        ),
    )
    args = parser.parse_args()

    if args.force_entry:
        print('WARNING: --force-entry will place a REAL order on the paper account.')

    engine, provider = build_local_engine(
        use_advisor=not args.no_advisor,
        force_entry=args.force_entry,
        force_entry_stop_pct=args.stop_pct,
        config_file=args.config,
    )
    advisor_state = 'on' if engine.advisor else 'off'
    mode = 'FORCE-ENTRY' if args.force_entry else 'normal'
    source = describe_source(file=args.config)
    trade_date = datetime.now(ET).date() + timedelta(days=args.cycle) if args.cycle else None
    print(
        f'Connected: {engine.broker.mode.value} account, advisor={advisor_state}, '
        f'strategy={mode}, config={source}'
        + (f', trade_date={trade_date} (cycle {args.cycle})' if trade_date else '')
    )

    def _one() -> None:
        record = run_once(engine, provider, trade_date=trade_date)
        line = f'[{record.ts.isoformat()}] {record.action}: {record.reason}'
        if record.alert:
            line += f'  ALERT: {record.alert}'
        print(line)

    if not args.loop:
        _one()
        return

    print(f'Looping every {args.interval:.0f}s (Ctrl-C to stop).')
    try:
        while True:
            _one()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print('Stopped.')


if __name__ == '__main__':
    main()
