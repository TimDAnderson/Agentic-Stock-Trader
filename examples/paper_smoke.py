"""Manual smoke test against the **real Alpaca paper endpoint**.

This is the one piece that needs network + credentials, so it is a script you
run by hand — not part of the test suite. It verifies connectivity, account
mode, and the order primitives (bracket entry, lookup, liquidation).

Setup:
    uv sync --extra broker
    export ALPACA_PAPER_API_KEY=...      # or ALPACA_API_KEY
    export ALPACA_PAPER_SECRET_KEY=...   # or ALPACA_SECRET_KEY

Usage:
    uv run python examples/paper_smoke.py                      # read-only: account + positions
    uv run python examples/paper_smoke.py --buy SPY 1 --stop 400 --take 600
    uv run python examples/paper_smoke.py --close             # liquidate everything

Paper only by design. You supply explicit stop/take prices so the script never
needs a market-data feed; pick values that bracket the current price.
"""

from __future__ import annotations

import argparse
from datetime import date

from trading_bot.broker import BracketOrder, BrokerMode, entry_id, load_credentials
from trading_bot.broker.alpaca import AlpacaBroker


def main() -> None:
    parser = argparse.ArgumentParser(description='Alpaca paper-endpoint smoke test.')
    parser.add_argument('--buy', nargs=2, metavar=('SYMBOL', 'QTY'), help='submit a bracket buy')
    parser.add_argument('--stop', type=float, help='stop-loss price for --buy')
    parser.add_argument('--take', type=float, help='take-profit price for --buy')
    parser.add_argument('--close', action='store_true', help='close all open positions')
    args = parser.parse_args()

    creds = load_credentials(BrokerMode.PAPER)
    broker = AlpacaBroker(BrokerMode.PAPER, creds)  # verifies the account at startup

    account = broker.get_account()
    print(f'Connected: account {account.account_id} ({account.mode.value})')
    print(f'  equity ${account.equity:,.2f}  cash ${account.cash:,.2f}')

    positions = broker.get_positions()
    print(f'Open positions: {len(positions)}')
    for p in positions:
        print(f'  {p.symbol}: {p.qty} @ ${p.avg_entry_price:.2f} (now ${p.current_price})')

    if args.buy:
        if args.stop is None:
            parser.error('--buy requires --stop (and optionally --take)')
        symbol, qty = args.buy[0].upper(), int(args.buy[1])
        order = BracketOrder(
            symbol=symbol,
            qty=qty,
            stop_loss_price=args.stop,
            take_profit_price=args.take,
            client_order_id=entry_id(date.today()),
        )
        result = broker.submit_bracket_buy(order)
        print(f'Submitted bracket buy: {result.symbol} x{result.qty} -> status {result.status}')
        print(f'  order id {result.id}  client_order_id {result.client_order_id}')

    if args.close:
        closed = broker.close_all_positions(cancel_orders=True)
        print(f'Closed {len(closed)} position(s): {[c.symbol for c in closed]}')


if __name__ == '__main__':
    main()
