from __future__ import annotations

import itertools
from copy import deepcopy

import numpy as np
import pandas as pd

from .metrics import TRADING_DAYS, perf_stats, period_summary, split_periods, zscore


def select_front_contracts(curve: pd.DataFrame) -> pd.DataFrame:
    df = curve.copy()
    df["dte"] = (df["expiry"] - df["date"]).dt.days
    df = df[df["dte"] > 3]
    if df.empty:
        raise ValueError("no non-expired contracts available")
    idx = df.groupby(["asset", "date"])["dte"].idxmin()
    front = df.loc[idx].sort_values(["asset", "date"]).reset_index(drop=True)
    front = front.rename(columns={"settle": "future_settle", "contract": "future_contract"})
    return front[["date", "asset", "future_contract", "expiry", "dte", "future_settle"]]


def build_signal_panel(curve: pd.DataFrame, long_prices: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    s_cfg = cfg["strategy"]
    front = select_front_contracts(curve)
    px = long_prices.copy()
    px = px.sort_values(["asset", "date"])
    px = px.groupby(["asset", "date"], as_index=False).last()
    panel = front.merge(px[["date", "asset", "symbol", "close"]], on=["date", "asset"], how="inner")
    panel = panel.rename(columns={"symbol": "long_symbol", "close": "long_close"})
    if panel.empty:
        raise ValueError("no overlapping dates between futures curve and long prices")

    panel["basis"] = panel["future_settle"] / panel["long_close"] - 1.0
    panel["basis_ann"] = panel["basis"] * TRADING_DAYS / panel["dte"].clip(lower=1)
    panel = panel.sort_values(["asset", "date"])
    panel["long_ret"] = panel.groupby("asset")["long_close"].pct_change().fillna(0.0)
    panel["future_ret"] = panel.groupby("asset")["future_settle"].pct_change().fillna(0.0)
    panel["pair_ret_raw"] = panel["long_ret"] - panel["future_ret"]
    basis_z_window = int(s_cfg.get("basis_z_window", 42))
    momentum_window = int(s_cfg.get("momentum_window", 21))
    panel["basis_z"] = panel.groupby("asset")["basis_ann"].transform(lambda x: zscore(x, basis_z_window)).fillna(0.0)
    panel["basis_change_5d"] = panel.groupby("asset")["basis_ann"].diff(5).fillna(0.0)
    panel["long_momentum"] = (
        panel.groupby("asset")["long_close"]
        .pct_change(momentum_window)
        .replace([np.inf, -np.inf], np.nan)
        .fillna(0.0)
    )
    panel["future_momentum"] = (
        panel.groupby("asset")["future_settle"]
        .pct_change(momentum_window)
        .replace([np.inf, -np.inf], np.nan)
        .fillna(0.0)
    )
    panel["realized_pair_vol"] = (
        panel.groupby("asset")["pair_ret_raw"]
        .transform(lambda x: x.rolling(int(cfg["strategy"]["vol_window"]), min_periods=10).std(ddof=0) * np.sqrt(TRADING_DAYS))
        .bfill()
        .fillna(0.0)
    )
    panel["carry_quality"] = (
        panel["basis_ann"].clip(lower=0.0)
        * (1.0 + panel["basis_z"].clip(lower=-1.0, upper=2.0) / 4.0)
        / panel["realized_pair_vol"].replace(0, np.nan)
    ).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return panel


def _params_from_cfg(cfg: dict) -> dict:
    s_cfg = cfg["strategy"]
    return {
        "min_basis_entry": float(s_cfg["min_basis_entry"]),
        "exit_basis": float(s_cfg["exit_basis"]),
        "basis_z_entry": float(s_cfg.get("basis_z_entry", -1.0)),
        "max_asset_weight": float(s_cfg["max_asset_weight"]),
        "gross_target": float(s_cfg["gross_target"]),
        "vol_target": float(s_cfg["vol_target"]),
        "max_pair_vol": float(s_cfg.get("max_pair_vol", 10.0)),
        "trend_floor": float(s_cfg.get("trend_floor", -10.0)),
        "use_trend_filter": bool(s_cfg.get("use_trend_filter", False)),
    }


def _cfg_with_params(cfg: dict, params: dict) -> dict:
    out = deepcopy(cfg)
    out["strategy"] = dict(out["strategy"])
    for key, value in params.items():
        out["strategy"][key] = value
    return out


def compute_positions(panel: pd.DataFrame, cfg: dict, params: dict | None = None) -> pd.DataFrame:
    p = _params_from_cfg(_cfg_with_params(cfg, params or {}))
    entry = float(p["min_basis_entry"])
    exit_basis = float(p["exit_basis"])
    basis_z_entry = float(p["basis_z_entry"])
    max_asset_weight = float(p["max_asset_weight"])
    gross_target = float(p["gross_target"])
    vol_target = float(p["vol_target"])
    max_pair_vol = float(p["max_pair_vol"])
    trend_floor = float(p["trend_floor"])
    use_trend_filter = bool(p["use_trend_filter"])

    out = []
    for asset, g in panel.groupby("asset", sort=True):
        active = False
        rows = []
        for _, row in g.sort_values("date").iterrows():
            b = float(row["basis_ann"])
            basis_z_ok = float(row.get("basis_z", 0.0)) >= basis_z_entry
            vol_ok = float(row.get("realized_pair_vol", 0.0) or 0.0) <= max_pair_vol
            trend_ok = (not use_trend_filter) or float(row.get("long_momentum", 0.0)) >= trend_floor
            entry_ok = b >= entry and basis_z_ok and vol_ok and trend_ok
            exit_ok = b <= exit_basis or not vol_ok or not trend_ok
            if not active and entry_ok:
                active = True
            elif active and exit_ok:
                active = False

            quality = max(float(row.get("carry_quality", 0.0) or 0.0), 0.0)
            raw_weight = min(max_asset_weight, quality) if active else 0.0
            vol = float(row.get("realized_pair_vol", 0.0) or 0.0)
            vol_scale = min(1.0, vol_target / vol) if vol > 0 else 0.0
            rows.append(
                {
                    **row.to_dict(),
                    "active": bool(active),
                    "entry_ok": bool(entry_ok),
                    "basis_z_ok": bool(basis_z_ok),
                    "vol_ok": bool(vol_ok),
                    "trend_ok": bool(trend_ok),
                    "raw_weight": raw_weight,
                    "vol_scale": vol_scale,
                }
            )
        out.extend(rows)

    pos = pd.DataFrame(out).sort_values(["date", "asset"])
    pos["target_weight"] = pos["raw_weight"] * pos["vol_scale"]
    gross = pos.groupby("date")["target_weight"].transform("sum").replace(0, np.nan)
    scaler = (gross_target / gross).clip(upper=1.0).fillna(0.0)
    pos["target_weight"] = (pos["target_weight"] * scaler).clip(0.0, max_asset_weight)
    return pos


def simulate(panel: pd.DataFrame, cfg: dict, params: dict | None = None) -> tuple[pd.Series, pd.DataFrame]:
    effective_cfg = _cfg_with_params(cfg, params or {})
    pos = compute_positions(panel, effective_cfg)
    s_cfg = effective_cfg["strategy"]
    carry_cost = float(s_cfg["carry_cost_bps"]) / 10000.0 / TRADING_DAYS
    financing = float(s_cfg["financing_bps"]) / 10000.0 / TRADING_DAYS
    roundtrip = (float(s_cfg["futures_roundtrip_bps"]) + float(s_cfg["long_leg_roundtrip_bps"])) / 10000.0

    pos = pos.sort_values(["asset", "date"])
    pos["prev_weight"] = pos.groupby("asset")["target_weight"].shift(1).fillna(0.0)
    pos["turnover"] = (pos["target_weight"] - pos["prev_weight"]).abs()
    pos["gross_daily_cost"] = pos["prev_weight"] * (carry_cost + financing)
    pos["trading_cost"] = pos["turnover"] * roundtrip
    pos["strategy_ret"] = (
        pos["prev_weight"] * pos["pair_ret_raw"]
        - pos["gross_daily_cost"]
        - pos["trading_cost"]
    )
    ret = pos.groupby("date")["strategy_ret"].sum().sort_index()
    return ret, pos


def parameter_grid(cfg: dict) -> list[dict]:
    model_cfg = cfg["strategy"].get("model_selection", {})
    grid = model_cfg.get("grid", {})
    if not grid:
        return [_params_from_cfg(cfg)]
    keys = sorted(grid.keys())
    values = [grid[k] for k in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


def grid_search(panel: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    periods = split_periods(panel["date"], cfg)
    min_validation_days = int(cfg["strategy"].get("model_selection", {}).get("min_validation_days", 30))
    rows = []
    for trial, params in enumerate(parameter_grid(cfg), start=1):
        returns, positions = simulate(panel, cfg, params)
        row = {"trial": trial, **params}
        for period, dates in periods.items():
            stats = perf_stats(returns.loc[returns.index.isin(dates)])
            for key, value in stats.items():
                row[f"{period}_{key}"] = value
        row["full_turnover"] = float(positions.groupby("date")["turnover"].sum().mean()) if not positions.empty else 0.0
        row["active_days"] = int((positions.groupby("date")["target_weight"].sum() > 0).sum()) if not positions.empty else 0
        row["valid_for_selection"] = bool(
            row.get("validation_n", 0) >= min_validation_days
            and row.get("train_n", 0) >= max(30, int(cfg["strategy"].get("min_train_days", 252)) // 4)
        )
        rows.append(row)
    return pd.DataFrame(rows)


def score_grid(grid: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    if grid.empty:
        return grid
    objective = cfg["strategy"].get("model_selection", {}).get("objective", "validation_sharpe")
    out = grid.copy()
    out["selection_score"] = (
        out.get(objective, out["validation_sharpe"]).astype(float)
        + 0.25 * out["validation_ann_return"].astype(float)
        + 0.10 * out["train_sharpe"].astype(float).clip(-5, 5)
        - 0.50 * out["validation_max_dd"].astype(float).abs()
        - 0.05 * out["full_turnover"].astype(float)
    )
    return out


def select_best(grid: pd.DataFrame, cfg: dict) -> dict:
    if grid.empty:
        return _params_from_cfg(cfg)
    scored = score_grid(grid, cfg)
    candidates = scored[scored["valid_for_selection"].astype(bool)].copy()
    if candidates.empty:
        candidates = scored.copy()
    best = candidates.sort_values(["selection_score", "validation_sharpe", "validation_ann_return"], ascending=False).iloc[0]
    grid_keys = set(cfg["strategy"].get("model_selection", {}).get("grid", {}).keys())
    if not grid_keys:
        return _params_from_cfg(cfg)
    return {k: best[k].item() if hasattr(best[k], "item") else best[k] for k in sorted(grid_keys)}


def run_research(curve: pd.DataFrame, long_prices: pd.DataFrame, cfg: dict, run_grid: bool | None = None) -> dict:
    panel = build_signal_panel(curve, long_prices, cfg)
    model_cfg = cfg["strategy"].get("model_selection", {})
    do_grid = bool(model_cfg.get("enabled", True)) if run_grid is None else bool(run_grid)
    grid = grid_search(panel, cfg) if do_grid else pd.DataFrame()
    grid = score_grid(grid, cfg) if do_grid else grid
    selected_params = select_best(grid, cfg) if do_grid else _params_from_cfg(cfg)
    effective_cfg = _cfg_with_params(cfg, selected_params)
    returns, positions = simulate(panel, effective_cfg)
    summary = period_summary(returns, cfg)
    return {
        "panel": panel,
        "returns": returns,
        "positions": positions,
        "summary": summary,
        "grid_results": grid,
        "selected_params": selected_params,
        "effective_config": effective_cfg,
    }
