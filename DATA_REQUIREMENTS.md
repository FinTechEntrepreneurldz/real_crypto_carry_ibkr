# Data Requirements

This repo does not download or invent deployable history. You must provide real historical futures curve data and long-leg prices.

## `data/cme_curve.csv`

Required columns:

```text
date,asset,contract,expiry,settle
```

Example row shape:

```text
2026-05-18,BTC,BIPZ30,2030-12-20,104250.00
```

Optional columns:

```text
exchange,multiplier,source
```

## `data/long_prices.csv`

Required columns:

```text
date,asset,symbol,close
```

Example row shape:

```text
2026-05-18,BTC,IBIT,62.11
```

## Accepted Sources

Default accepted source labels:

```text
cme_datamine
cme_datamine_api
databento
ibkr_historical
cqg
rithmic
```

Run `scripts/build_artifact.py --data-source <source>` with one of those labels, or edit `config/default.yaml` if you have another real institutional data source.

Blocked labels include:

```text
synthetic
proxy
sample
demo
yfinance
yahoo
```

## Why This Is Strict

A high Sharpe from fake or proxy data is worse than useless because it creates false confidence. The artifact builder will still write diagnostics for bad sources, but deployability stays blocked.
