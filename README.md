# Real Crypto Carry IBKR

Real-data-only crypto futures carry research and execution tooling for Interactive Brokers.

This repo is intentionally allergic to fake performance. It does **not** ship synthetic backtest artifacts, proxy Yahoo curves, or hard-coded Sharpe claims. A strategy artifact becomes deployable only when it is built from accepted real data sources and passes out-of-sample gates.

## What This Trades

The default implementation studies and exports a conservative cash-and-carry style book:

- BTC: long `IBIT` or configured spot/ETP leg, short regulated BTC futures
- ETH: long `ETHA` or configured spot/ETP leg, short regulated ETH futures
- Optional calendar spread rows can be researched from the same curve data, but live execution defaults to the cash-and-carry pair.

Supported futures roots are configurable. Defaults include:

- Coinbase Derivatives perpetual-style futures through IBKR: `BIP` and `ETP`
- CME micro futures: `MBT` and `MET`

## Non-Synthetic Data Contract

You must supply real historical data:

```text
data/cme_curve.csv
data/long_prices.csv
```

`cme_curve.csv` required columns:

```text
date,asset,contract,expiry,settle
```

Optional columns:

```text
exchange,multiplier,source
```

`long_prices.csv` required columns:

```text
date,asset,symbol,close
```

Accepted deployable data sources are configured in `config/default.yaml`. The defaults are:

```text
cme_datamine,cme_datamine_api,databento,ibkr_historical,cqg,rithmic
```

Anything marked `synthetic`, `proxy`, `sample`, `demo`, `yfinance`, or `yahoo` is blocked from deployable artifacts.

## Quick Start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

python scripts/build_artifact.py \
  --curve-csv data/cme_curve.csv \
  --long-prices-csv data/long_prices.csv \
  --out-dir artifacts/latest \
  --data-source databento
```

The final output is:

```text
artifacts/latest/artifacts.zip
```

The zip contains:

- `metadata.json`
- `status.json`
- `config.json`
- `execution_plan_latest.json`
- `performance_summary.csv`
- `daily_returns.csv`
- `signals.csv`
- `positions.csv`
- `manifest.json`

## Deployment Gate

The artifact status is `DEPLOYABLE_IBKR_CARRY` only if all are true:

- Data source is accepted real data.
- Required curve and long-leg prices are present.
- Test Sharpe is at least `2.0`.
- Test annualized return is at least `15%`.
- Test max drawdown is no worse than `-30%`.
- Latest execution plan has valid symbols/contracts and non-stale prices.

Otherwise the artifact is still written, but marked `RESEARCH_ONLY_NOT_DEPLOYABLE`.

## IBKR Execution

Execution is dry-run by default.

```bash
python scripts/run_ibkr_rebalance.py \
  --artifact-dir artifacts/latest \
  --dry-run
```

Paper order submission requires:

```text
IBKR_ACCOUNT=DUxxxxxxx
IBKR_HOST=127.0.0.1
IBKR_PORT=7497
IBKR_REQUIRE_PAPER_TRADING=true
IBKR_SUBMIT_ORDERS=true
```

Live order submission additionally requires:

```text
IBKR_REQUIRE_PAPER_TRADING=false
IBKR_ALLOW_LIVE_TRADING=true
IBKR_LIVE_TRADING_ACK=I_UNDERSTAND_THIS_SUBMITS_REAL_ORDERS
```

Do not unlock live trading until paper execution has matched the intended behavior over multiple runs.

## Reality Check

No code can guarantee a high Sharpe or make-money outcome. This repo is built to avoid self-deception: it blocks synthetic/proxy data, separates validation from test, includes costs and financing assumptions, and refuses deployability unless the real OOS numbers meet the configured gates.
