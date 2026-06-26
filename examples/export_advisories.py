"""Export the LLM advisory reasoning from the DynamoDB run records.

CloudWatch only has the one-line run summary; the full Tree-of-Thought blob
(bull/bear/neutral theses, the evaluator's verdict, and llm_calls) is stored on
each run record in DynamoDB (DECISIONS.md §9). This dumps it for the runs that
actually invoked the advisor (i.e. the strategy proposed a buy), so you can see
*why* it vetoed — and tell a real veto (llm_calls > 0) from a silent failure
(llm_calls == 0, which safe-defaults to VETO).

    uv run --extra aws python examples/export_advisories.py
    uv run --extra aws python examples/export_advisories.py --days 5 --out vetoes.txt
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from trading_bot.state.dynamodb import DynamoStateRepository

DEFAULT_TABLE = 'TradingBotPaper-StateTable9728C7E5-1NK7RZ8BH7KLM'


def render(table: str, days: int, region: str) -> tuple[str, int]:
    repo = DynamoStateRepository(table, region_name=region)
    today = datetime.now(UTC).date()
    dates = sorted(today - timedelta(days=i) for i in range(days))

    lines: list[str] = [
        f'Advisory reasoning — last {days} day(s)',
        f'Table: {table}',
        '=' * 88,
        '',
    ]
    count = 0
    for d in dates:
        for run in sorted(repo.list_runs(d), key=lambda r: r.ts):
            adv = run.advisory
            if not adv:  # only runs that actually invoked the advisor
                continue
            count += 1
            calls = adv.get('llm_calls', 0)
            flag = '' if calls else '   <-- llm_calls=0: advisor ERRORED, defaulted to VETO'
            lines.append(f'{run.ts.isoformat()}  action={run.action}')
            lines.append(f'  recommendation : {adv.get("recommendation")}{flag}')
            lines.append(f'  llm_calls      : {calls}')
            lines.append(f'  run reason     : {run.reason}')
            lines.append(f'  evaluator      : {adv.get("reason", "")}')
            for branch in adv.get('branches', []):
                thesis = str(branch.get('thesis', '')).strip().replace('\n', ' ')
                lines.append(f'  {str(branch.get("stance", "")).upper():<8}: {thesis}')
            lines.append('')
    if count == 0:
        lines.append('(no advisory runs in the window — the strategy never proposed a buy)')
    return '\n'.join(lines) + '\n', count


def main() -> None:
    parser = argparse.ArgumentParser(description='Export advisory reasoning from DynamoDB.')
    parser.add_argument('--table', default=DEFAULT_TABLE, help='DynamoDB state table name')
    parser.add_argument('--days', type=int, default=2, help='look-back window in days')
    parser.add_argument('--region', default='us-east-1', help='AWS region')
    parser.add_argument('--out', default=None, help='also write to this file')
    args = parser.parse_args()

    text, count = render(args.table, args.days, args.region)
    print(text)
    if args.out:
        with open(args.out, 'w') as fh:
            fh.write(text)
        print(f'Wrote {count} advisory runs to {args.out}')


if __name__ == '__main__':
    main()
