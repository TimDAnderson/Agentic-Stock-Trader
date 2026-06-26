"""Export trading-bot run summaries from CloudWatch Logs into a readable file.

Pulls the structured-JSON run summaries the Lambda logs (DECISIONS.md §9) for a
time window and writes a document with (1) a one-line-per-run table and (2) the
full pretty-printed entries — each stamped with the log timestamp.

    uv run --extra aws python examples/export_runs.py                 # last 2 days
    uv run --extra aws python examples/export_runs.py --days 7
    uv run --extra aws python examples/export_runs.py --out runs.txt --log-group <name>

Needs the ``aws`` extra and AWS credentials with CloudWatch Logs read access.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from typing import Any

# The paper stack's Lambda log group (override with --log-group for live).
DEFAULT_LOG_GROUP = 'TradingBotPaper-FnLogs3EE1EAE0-tnA46tVXZkJZ'


def _iso(ms: int) -> str:
    """Epoch milliseconds -> 'YYYY-MM-DDTHH:MM:SS.mmmZ' (UTC), matching the logs."""
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def fetch_runs(log_group: str, days: float, region: str | None) -> list[dict[str, Any]]:
    """Return run-summary events (each with its log timestamp), oldest first."""
    import boto3

    client = boto3.client('logs', region_name=region)
    start = int(time.time() * 1000) - int(days * 24 * 60 * 60 * 1000)
    paginator = client.get_paginator('filter_log_events')

    runs: list[dict[str, Any]] = []
    for page in paginator.paginate(logGroupName=log_group, startTime=start):
        for ev in page.get('events', []):
            msg = str(ev['message']).rstrip('\n')
            brace = msg.find('{')
            if brace == -1:
                continue
            try:
                summary = json.loads(msg[brace:])
            except json.JSONDecodeError:
                continue
            if 'action' not in summary or 'trade_date' not in summary:
                continue  # only our run summaries, not START/END/REPORT lines
            runs.append(
                {
                    'ts_ms': int(ev['timestamp']),
                    'ts': _iso(int(ev['timestamp'])),
                    'header': msg[:brace].rstrip(),  # the raw '[INFO]\t<ts>\t<reqid>' line
                    'summary': summary,
                }
            )
    runs.sort(key=lambda r: r['ts_ms'])
    return runs


def render(runs: list[dict[str, Any]], log_group: str, days: float) -> str:
    generated = _iso(int(time.time() * 1000))
    out: list[str] = [
        f'Trading-bot runs — last {days:g} day(s): {len(runs)} runs',
        f'Log group: {log_group}',
        f'Generated: {generated}',
        '=' * 88,
        '',
    ]
    if not runs:
        out.append('(no run summaries found in the window)')
        return '\n'.join(out) + '\n'

    # Quick-scan table, timestamp first.
    out.append('SUMMARY')
    out.append(f'{"TIMESTAMP (UTC)":<26}{"ACTION":<14}{"TRADE_DATE":<13}REASON')
    out.append('-' * 88)
    for r in runs:
        s = r['summary']
        out.append(
            f'{r["ts"]:<26}{str(s.get("action", "")):<14}'
            f'{str(s.get("trade_date", "")):<13}{s.get("reason", "")}'
        )
    out.append('')
    out.append('=' * 88)
    out.append('')

    # Full entries.
    out.append('DETAIL')
    out.append('')
    for r in runs:
        out.append(r['header'])
        out.append(json.dumps(r['summary'], indent=4))
        out.append('')
    return '\n'.join(out) + '\n'


def main() -> None:
    parser = argparse.ArgumentParser(description='Export trading-bot runs from CloudWatch Logs.')
    parser.add_argument('--log-group', default=DEFAULT_LOG_GROUP, help='CloudWatch log group name')
    parser.add_argument('--days', type=float, default=2.0, help='look-back window in days')
    parser.add_argument(
        '--out', default=None, help='output file (default paper_runs_last_<days>_days.txt)'
    )
    parser.add_argument('--region', default=None, help='AWS region (default from your config)')
    args = parser.parse_args()

    out_path = args.out or f'paper_runs_last_{args.days:g}_days.txt'
    runs = fetch_runs(args.log_group, args.days, args.region)
    with open(out_path, 'w') as fh:
        fh.write(render(runs, args.log_group, args.days))
    print(f'Wrote {len(runs)} runs to {out_path}')


if __name__ == '__main__':
    main()
