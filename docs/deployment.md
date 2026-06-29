# Deploying to AWS

The bot ships as a container-image Lambda driven by EventBridge, with DynamoDB
for state, SSM for config + secrets, and CloudWatch alarms → SNS. One
parameterized `TradingBotStack` is instantiated twice:

- **`TradingBotPaper`** — staging (fake money). Same handler the live stack runs.
- **`TradingBotLive`** — production (real money), created behind `-c live=1`.

Each stack gets its **own** DynamoDB table, secrets, SSM config, Lambda,
schedules, and alarms — completely separate. The Lambda runs the *same*
`build_engine` wiring as the local runner; only the secret/config *source*
differs (DECISIONS.md §11).

## Prerequisites

```bash
npm install -g aws-cdk          # the CDK CLI (Toolkit)
uv sync --extra infra           # aws-cdk-lib for the Python app
aws configure                   # credentials for the target account
cdk bootstrap                   # once per account/region
```

## Paper (staging)

```bash
make deploy-paper               # = cd infra && cdk deploy TradingBotPaper
```

This builds the container image and creates the DynamoDB table, the SSM config
parameter (from `examples/strategy_config.example.json` — or point
`STRATEGY_CONFIG_FILE` at your tuned file), the EventBridge schedules, and alarms.
It does **not** create the secrets (see below). The stack prints a
`SecretSsmParams` output listing the SecureString parameter names the Lambda reads.

### Secrets: SSM SecureStrings you create yourself

Secrets are **SSM Parameter Store `SecureString` parameters** — you create and
rotate them out-of-band (never in code/CloudFormation). CDK only grants the Lambda
permission to **read + KMS-decrypt** them. Create these three under
`/trading-bot/paper/`:

```bash
aws ssm put-parameter --type SecureString --name /trading-bot/paper/ALPACA_PAPER_API_KEY    --value 'PK...'
aws ssm put-parameter --type SecureString --name /trading-bot/paper/ALPACA_PAPER_SECRET_KEY --value '...'
aws ssm put-parameter --type SecureString --name /trading-bot/paper/OPENROUTER_API_KEY      --value 'sk-or-...'
```

The **parameter's last path segment is the env-var name the code reads**
(`/trading-bot/paper/ALPACA_PAPER_API_KEY` → `ALPACA_PAPER_API_KEY`). The handler
loads them into the environment at cold start, so `load_credentials` /
`OpenRouterLLM` find their keys exactly as they do locally. Until they exist the
run fails loudly (safe) rather than trading blind. Rotating = another
`put-parameter --overwrite`; **no redeploy** (applies on the next cold start).

> Using the default `aws/ssm` KMS key needs no extra setup — the stack grants
> `kms:Decrypt` scoped to the SSM service. A **customer-managed** KMS key would
> also need that key's policy to allow the Lambda role.

The non-secret **`StrategyConfig` is created by CDK** (plain SSM `String`) and is
a zero-deploy tuning lever — edit the parameter to retune; the handler reads it
each run.

### Verify

```bash
aws lambda invoke --function-name <FunctionName-from-outputs> /dev/stdout
```

A `DO_NOTHING` result (or "Market closed (calendar gate)" off-hours) is the
normal, correct outcome — the schedule fires often; the gate + strategy keep most
runs idle.

## Go live (production)

The live stack is the **same `TradingBotStack` with `mode='live'`** (behind the
`live` CDK context flag). Two strong recommendations from DECISIONS.md: deploy it
in a **separate AWS account** (§3, blast-radius isolation) and go live at
**minimum share size first** (§14).

> ⚠️ **Do not go live until paper has run cleanly for a sustained period** (§10) —
> review the logged decisions/trades against expectations. The live stack trades
> **real money**. `--force-entry` must never be used against it.

**1. Create the live secrets** (note `ALPACA_LIVE_*` — live and paper keys are
**not** interchangeable; these live under `/trading-bot/live/`):

```bash
aws ssm put-parameter --type SecureString --name /trading-bot/live/ALPACA_LIVE_API_KEY    --value 'AK...'
aws ssm put-parameter --type SecureString --name /trading-bot/live/ALPACA_LIVE_SECRET_KEY --value '...'
aws ssm put-parameter --type SecureString --name /trading-bot/live/OPENROUTER_API_KEY     --value 'sk-or-...'
```

**2. Bootstrap the target account/region** (once; skip if already bootstrapped):

```bash
cd infra && cdk bootstrap aws://<LIVE_ACCOUNT_ID>/us-east-1
```

**3. Deploy** (the `-c live=1` context creates the live stack; the live data feed
defaults to `sip`):

```bash
export OPENROUTER_MODEL=anthropic/claude-3.5-haiku   # optional; same knobs as paper
export ALARM_EMAIL=you@example.com                   # optional; confirm the email after
make deploy-live                                     # = cdk deploy TradingBotLive -c live=1
```

**4. Verify** as with paper, using the live function name from the stack outputs:

```bash
aws lambda invoke --function-name <TradingBotLive FunctionName> /dev/stdout
```

The engine refuses to start if the keys don't match the `live` endpoint; config is
the SSM `String` zero-deploy lever; the inspection scripts work by pointing
`--log-group` / `--table` at the live stack's resources
(`aws cloudformation describe-stacks --stack-name TradingBotLive`).

## Scheduling

EventBridge cron is a *coarse* trigger (UTC); the in-handler **market-calendar
gate** makes it correct across holidays/half-days/DST. Three rules per stack:

- **`SessionSchedule`** — every 15 min on weekdays across the widest ET-session-in-
  UTC window (entries + management).
- **`MocBackstop`** — ~15:47 ET, places the resting market-on-close sell.
- **`EodLiquidation`** — ~15:56 ET, the immediate near-close liquidation.

To pause trading without tearing down the stack, disable the rules
(`aws events disable-rule --name <RuleName>`) or leave the secrets unpopulated.

## EOD safety: two independent backstops

Flat-by-close is enforced two ways, so a single failure can't strand a position:

1. **Resting MOC (market-on-close)** — at ~15:47 ET the engine cancels the bracket
   legs and places a market-on-close sell that the exchange fills in the closing
   auction **even if no later Lambda runs** ("survives a dead Lambda"). Window/
   toggle: `place_moc_after`, `moc_cutoff`, `moc_backstop_enabled` in
   `StrategyConfig`.
2. **Scheduled liquidation** — at ~15:56 ET the strategy's `force_exit_after`
   triggers `close_all_positions`, an immediate market exit that also cancels the
   resting MOC. If runs keep firing this is what flattens you; the MOC is the
   insurance for when they don't.

Trade-off (by design): between the MOC placement (~15:47) and the close there is
no hard stop — the bracket was cancelled so the MOC can't be left selling shares a
stop already closed (which would open a short). It's a ~10-minute window on a
buy-once/flat-by-close bot.

To **watch it on paper:** force an entry mid-session and leave the loop running
into the afternoon, or invoke the deployed Lambda after 15:45 ET — a run logs
`action: MOC` once, then the position flattens at the close.

## Alarms

CloudWatch alarms (Lambda errors, near-timeout duration) publish to an SNS topic
per stack. Set `ALARM_EMAIL` before deploy to add an email subscription (confirm
the link AWS emails you). The alarms fire to the topic regardless; the email is
just a delivery channel.
