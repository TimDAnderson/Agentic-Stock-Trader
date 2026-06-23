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

The calendar gate means a run outside market hours correctly does nothing.
"""

from __future__ import annotations

import argparse
import time

from trading_bot.runner import build_local_engine, run_once


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
    args = parser.parse_args()

    if args.force_entry:
        print('WARNING: --force-entry will place a REAL order on the paper account.')

    engine, provider = build_local_engine(
        use_advisor=not args.no_advisor, force_entry=args.force_entry
    )
    advisor_state = 'on' if engine.advisor else 'off'
    mode = 'FORCE-ENTRY' if args.force_entry else 'normal'
    print(
        f'Connected: {engine.broker.mode.value} account, advisor={advisor_state}, strategy={mode}'
    )

    def _one() -> None:
        record = run_once(engine, provider)
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
