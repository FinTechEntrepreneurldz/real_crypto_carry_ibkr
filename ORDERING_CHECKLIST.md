# IBKR Ordering Checklist

## Before Any Order Submission

1. Build an artifact from accepted real data.
2. Confirm `status.json` says `DEPLOYABLE_IBKR_CARRY`.
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
