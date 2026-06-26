"""Confirm the OpenRouter model works before baking it into a deploy.

Two checks, both against the **real** OpenRouter API using the same client the
Lambda uses:

1. a one-shot connectivity ping (key + model slug + network), and
2. the full Tree-of-Thought advisory on a synthetic bullish buy — the exact path
   that runs in production (bull/bear/neutral → evaluator → PROCEED/VETO).

A non-zero ``llm_calls`` in check 2 proves real calls were made (the advisor
safe-defaults to VETO on any error, so a bad model would otherwise look like a
silent "veto everything"). Set the model the same way the deploy does:

    export OPENROUTER_API_KEY=sk-or-...
    export OPENROUTER_MODEL=anthropic/claude-3.5-haiku   # else gpt-4o-mini
    uv run --extra reasoning python examples/check_model.py
"""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from trading_bot.domain.decisions import EntryAction, EntryDecision
from trading_bot.domain.market_state import Indicators, MarketState
from trading_bot.reasoning.openrouter import OpenRouterLLM
from trading_bot.reasoning.tot import ToTAdvisor

ET = ZoneInfo('America/New_York')


def _synthetic_buy() -> tuple[MarketState, EntryDecision]:
    state = MarketState(
        as_of=datetime.now(ET),
        equity=100_000.0,
        indicators={
            'QQQ': Indicators(
                symbol='QQQ',
                price=400.0,
                vwap=399.0,
                ema=398.0,
                rsi=58.0,
                macd_hist=0.5,
                atr=1.2,
                relative_volume=1.3,
            )
        },
    )
    decision = EntryDecision(
        action=EntryAction.BUY_BULLISH,
        qty=12,
        stop_loss_price=398.2,
        take_profit_price=403.6,
        reason='synthetic bullish entry for model smoke test',
    )
    return state, decision


def main() -> None:
    model = os.environ.get('OPENROUTER_MODEL', 'openai/gpt-4o-mini')
    print(f'Model: {model}')
    llm = OpenRouterLLM(model=model)

    print('\n[1/2] Connectivity ping...')
    reply = llm.complete('You are a connectivity test.', 'Reply with the single word: OK')
    print(f'  reply: {reply.strip()[:80]!r}')

    print('\n[2/2] Full ToT advisory on a synthetic bullish buy...')
    state, decision = _synthetic_buy()
    advisory = ToTAdvisor(llm).advise(state, decision)
    print(f'  recommendation: {advisory.recommendation.value}')
    print(f'  llm_calls:      {advisory.llm_calls}')
    print(f'  reason:         {advisory.reason.strip()[:200]}')

    if advisory.llm_calls == 0:
        print(
            '\nWARNING: 0 LLM calls — the advisor errored and defaulted to VETO. '
            'Check the model slug / key / network above.'
        )
    else:
        print('\nOK: the model is reachable and the advisory path runs.')


if __name__ == '__main__':
    main()
