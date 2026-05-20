# IBKR Ordering Checklist

## Before Any Order Submission

1. Build an artifact from accepted real data.
2. Confirm `status.json` says `DEPLOYABLE_IBKR_ETF_REGIME` or `DEPLOYABLE_IBKR_CARRY`.
3. Start TWS or IB Gateway on the same machine that will run the script.
4. Enable API socket clients.
5. Keep paper trading on first:

```text
IBKR_REQUIRE_PAPER_TRADING=true
IBKR_PORT=7497
```

Use `4002` instead of `7497` for paper IB Gateway.

6. Set the account explicitly:

```text
IBKR_ACCOUNT=DUxxxxxxx
```

Paper-only execution now rejects accounts that do not start with `DU`.

7. Validate contracts:

```bash
python scripts/validate_ibkr_contracts.py
```

8. Dry-run first:

```bash
python scripts/run_ibkr_rebalance.py --artifact-dir artifacts/latest --dry-run
```

## Paper Submission

```text
IBKR_SUBMIT_ORDERS=true
IBKR_REQUIRE_PAPER_TRADING=true
IBKR_ALLOW_MARKET_ORDERS=false
IBKR_MAX_FUTURES_CONTRACTS_PER_RUN=1
```

Then run:

```bash
python scripts/run_ibkr_rebalance.py --artifact-dir artifacts/latest
```

## Daily GitHub Actions Paper Rebalance

The workflow `.github/workflows/ibkr-paper-rebalance.yml` is designed for a self-hosted runner on the same machine or network as TWS/IB Gateway. GitHub-hosted runners cannot connect to your local `127.0.0.1:7497` TWS session.

Required runner label:

```text
self-hosted, ibkr-paper
```

Required GitHub secret:

```text
IBKR_ACCOUNT=DUxxxxxxx
```

Optional GitHub variables:

```text
IBKR_HOST=127.0.0.1
IBKR_PORT=7497
IBKR_CLIENT_ID=17
IBKR_HISTORY_CLIENT_ID=92
IBKR_LONG_LEG_ORDER_TYPE=LIMIT
IBKR_LONG_LEG_LIMIT_OFFSET_BPS=5
IBKR_ORDER_WAIT_SECONDS=45
IBKR_CANCEL_UNFILLED=true
```

The workflow:

1. Pulls fresh IBKR historical prices.
2. Builds `artifacts/latest/artifacts.zip`.
3. Prints `status.json` and `execution_plan_latest.json`.
4. Runs a dry run.
5. Submits paper delta orders only if the artifact is deployable and paper safety checks pass.

The workflow checks current IBKR positions and open orders before submitting. If the account is already at target or has matching open orders, it skips instead of stacking duplicate orders.

It runs Monday-Friday at `13:30 UTC` and `14:30 UTC` with a New York-time guard, so only the run that lands in the `9 AM America/New_York` hour continues. It can also be triggered manually from the Actions tab.

The default workflow order type is paper-market (`IBKR_LONG_LEG_ORDER_TYPE=MKT`, `IBKR_ALLOW_MARKET_ORDERS=true`). Override those repo variables if you want limit orders instead.

## Live Unlock

Only after paper runs are verified:

```text
IBKR_REQUIRE_PAPER_TRADING=false
IBKR_ALLOW_LIVE_TRADING=true
IBKR_LIVE_TRADING_ACK=I_UNDERSTAND_THIS_SUBMITS_REAL_ORDERS
```

Use live TWS port `7496` or live IB Gateway port `4001`.

## Defaults That Protect You

- Futures leg is submitted before long leg.
- Long leg submits only after the futures hedge gets at least a partial fill.
- Orders are limit orders by default.
- Market orders are blocked unless explicitly enabled.
- A non-deployable artifact cannot be executed by `run_ibkr_rebalance.py`.
