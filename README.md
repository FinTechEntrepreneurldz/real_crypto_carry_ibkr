# Real Crypto Carry IBKR

Real-data-only crypto futures carry and crypto ETF regime research/execution tooling for Interactive Brokers.

This repo is intentionally allergic to fake performance. It does **not** ship synthetic backtest artifacts, proxy Yahoo curves, or hard-coded Sharpe claims. A strategy artifact becomes deployable only when it is built from accepted real data sources and passes out-of-sample gates.

## What This Trades

The default implementation now exports a regime-aware ETF relative-momentum book because the current real BIP/ETP futures basis sample is short and low-yielding:

- Uses real IBKR historical prices for `IBIT` and `ETHA`.
- Uses broad BTC/ETH ETF momentum to detect bear regimes.
- In bear regimes, shorts the weaker ETF with a partial long hedge.
- In recovery/bull regimes, trades partial BTC/ETH relative momentum.
- Exports stock-only IBKR orders; the futures leg is `null`.

The carry implementation is still available for a real futures-curve book:

- BTC: long `IBIT` or configured spot/ETP leg, short regulated BTC futures
- ETH: long `ETHA` or configured spot/ETP leg, short regulated ETH futures
- Optional calendar spread rows can be researched from the same curve data, but live execution defaults to the cash-and-carry pair.

Supported futures roots are configurable. Defaults include:

- Coinbase Derivatives perpetual-style futures through IBKR: `BIP` and `ETP`
- CME micro futures: `MBT` and `MET`

## Model Upgrade

The codebase includes two model paths:

- `etf_regime_relative_momentum`, the default real-data ETF regime strategy.
- `carry`, the futures cash-and-carry strategy that should be used when you have a longer real CME/Coinbase futures curve.

The carry model supports validation-first parameter selection:

- Builds basis, annualized basis, basis z-score, basis change, long-leg momentum, futures momentum, realized pair volatility, and carry quality features.
- Runs a grid over entry/exit basis thresholds, basis z-score entry, volatility targets, pair-volatility caps, gross exposure, per-asset caps, and optional trend filters.
- Selects parameters using train/validation only.
- Reports untouched test performance and refuses deployment unless the real OOS gate passes.

This is designed to improve the model without lying to you. If the real test set does not clear the gate, the artifact remains research-only.

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

With TWS/Gateway running, you can pull the default IBKR historical inputs first:

```bash
python scripts/pull_ibkr_history.py
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
- `selected_params.json`
- `grid_results.csv`
- `performance_summary.csv`
- `daily_returns.csv`
- `signals.csv`
- `positions.csv`
- `manifest.json`

## Deployment Gate

The artifact status is `DEPLOYABLE_IBKR_ETF_REGIME` for the default ETF regime model or `DEPLOYABLE_IBKR_CARRY` for the carry model only if all are true:

- Data source is accepted real data.
- Required curve and long-leg prices are present.
- Test Sharpe is at least `3.0`.
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
