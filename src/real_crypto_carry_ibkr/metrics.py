from __future__ import annotations

import math

import numpy as np
import pandas as pd


TRADING_DAYS = 365.25


def max_drawdown(returns: pd.Series) -> float:
    r = returns.dropna().astype(float)
    if r.empty:
        return 0.0
    equity = (1.0 + r).cumprod()
    dd = equity / equity.cummax() - 1.0
    return float(dd.min())


def perf_stats(returns: pd.Series) -> dict[str, float]:
    r = returns.dropna().astype(float)
    if r.empty:
        return {
            "ann_return": 0.0,
            "ann_vol": 0.0,
            "sharpe": 0.0,
            "sortino": 0.0,
            "max_dd": 0.0,
            "hit_rate": 0.0,
            "n": 0,
        }
    total = float((1.0 + r).prod() - 1.0)
    years = max(len(r) / TRADING_DAYS, 1e-9)
    ann_return = float((1.0 + total) ** (1.0 / years) - 1.0)
    ann_vol = float(r.std(ddof=0) * math.sqrt(TRADING_DAYS))
    downside = r[r < 0].std(ddof=0) * math.sqrt(TRADING_DAYS)
    sharpe = ann_return / ann_vol if ann_vol > 0 else 0.0
    sortino = ann_return / downside if downside and downside > 0 else 0.0
    return {
        "ann_return": ann_return,
        "ann_vol": ann_vol,
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "max_dd": max_drawdown(r),
        "hit_rate": float((r > 0).mean()),
        "n": int(len(r)),
    }


def split_periods(index: pd.Index, cfg: dict) -> dict[str, pd.Index]:
    dates = pd.Index(sorted(pd.to_datetime(index).unique()))
    n = len(dates)
    train_end = int(n * float(cfg["strategy"]["train_frac"]))
    val_end = int(n * (float(cfg["strategy"]["train_frac"]) + float(cfg["strategy"]["validation_frac"])))
    return {
        "train": dates[:train_end],
        "validation": dates[train_end:val_end],
        "test": dates[val_end:],
        "full": dates,
    }


def period_summary(returns: pd.Series, cfg: dict) -> pd.DataFrame:
    periods = split_periods(returns.index, cfg)
    rows = []
    for name, idx in periods.items():
        rows.append({"period": name, **perf_stats(returns.loc[returns.index.isin(idx)])})
    return pd.DataFrame(rows)


def zscore(s: pd.Series, window: int) -> pd.Series:
    m = s.rolling(window, min_periods=max(5, window // 3)).mean()
    sd = s.rolling(window, min_periods=max(5, window // 3)).std(ddof=0)
    return (s - m) / sd.replace(0, np.nan)
