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
    if "price_role" not in px.columns:
        px["price_role"] = "long"
    px["price_role"] = px["price_role"].astype(str).str.lower().str.strip()
    long_px = (
        px[px["price_role"].isin(["long", "etf", "execution"])]
        .sort_values(["asset", "date", "price_role"])
        .groupby(["asset", "date"], as_index=False)
        .last()
    )
    spot_px = (
        px[px["price_role"].isin(["spot", "underlying", "reference"])]
        .sort_values(["asset", "date", "price_role"])
        .groupby(["asset", "date"], as_index=False)
        .last()
    )
    if spot_px.empty:
        spot_px = long_px.copy()
        spot_px["price_role"] = "long_fallback"
    panel = front.merge(long_px[["date", "asset", "symbol", "close"]], on=["date", "asset"], how="inner")
    panel = panel.rename(columns={"symbol": "long_symbol", "close": "long_close"})
    panel = panel.merge(spot_px[["date", "asset", "symbol", "close"]], on=["date", "asset"], how="inner")
    panel = panel.rename(columns={"symbol": "spot_symbol", "close": "spot_close"})
    if panel.empty:
        raise ValueError("no overlapping dates between futures curve and long prices")

    panel["basis"] = panel["future_settle"] / panel["spot_close"] - 1.0
    panel["basis_ann"] = panel["basis"] * TRADING_DAYS / panel["dte"].clip(lower=1)
    panel = panel.sort_values(["asset", "date"])
    panel["long_ret"] = panel.groupby("asset")["long_close"].pct_change().fillna(0.0)
    panel["future_ret"] = panel.groupby("asset")["future_settle"].pct_change().fillna(0.0)
    panel["pair_ret_raw"] = panel["long_ret"] - panel["future_ret"]
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


def build_price_panel(long_prices: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    s_cfg = cfg["strategy"]
    px = long_prices.copy()
    px = px.sort_values(["asset", "date"])
    if "price_role" not in px.columns:
        px["price_role"] = "long"
    px["price_role"] = px["price_role"].astype(str).str.lower().str.strip()
    long_px = (
        px[px["price_role"].isin(["long", "etf", "execution"])]
        .sort_values(["asset", "date", "price_role"])
        .groupby(["asset", "date"], as_index=False)
        .last()
    )
    if long_px.empty:
        raise ValueError("long prices CSV has no ETF/execution price rows for price-only model")
    spot_px = (
        px[px["price_role"].isin(["spot", "underlying", "reference"])]
        .sort_values(["asset", "date", "price_role"])
        .groupby(["asset", "date"], as_index=False)
        .last()
    )
    panel = long_px[["date", "asset", "symbol", "close"]].rename(
        columns={"symbol": "long_symbol", "close": "long_close"}
    )
    if not spot_px.empty:
        panel = panel.merge(spot_px[["date", "asset", "symbol", "close"]], on=["date", "asset"], how="left")
        panel = panel.rename(columns={"symbol": "spot_symbol", "close": "spot_close"})
    else:
        panel["spot_symbol"] = panel["long_symbol"]
        panel["spot_close"] = panel["long_close"]
    panel["spot_symbol"] = panel["spot_symbol"].fillna(panel["long_symbol"])
    panel["spot_close"] = panel["spot_close"].fillna(panel["long_close"])
    panel["future_contract"] = ""
    panel["expiry"] = pd.NaT
    panel["dte"] = np.nan
    panel["future_settle"] = np.nan
    panel["basis"] = 0.0
    panel["basis_ann"] = 0.0
    panel = panel.sort_values(["asset", "date"])
    panel["long_ret"] = panel.groupby("asset")["long_close"].pct_change().fillna(0.0)
    panel["future_ret"] = 0.0
    panel["pair_ret_raw"] = panel["long_ret"]

    basis_z_window = int(s_cfg.get("basis_z_window", 42))
    momentum_window = int(s_cfg.get("momentum_window", 21))
    vol_window = int(s_cfg.get("vol_window", 42))
    panel["basis_z"] = 0.0
    panel["basis_change_5d"] = 0.0
    panel["long_momentum"] = (
        panel.groupby("asset")["long_close"]
        .pct_change(momentum_window)
        .replace([np.inf, -np.inf], np.nan)
        .fillna(0.0)
    )
    panel["future_momentum"] = 0.0
    panel["realized_pair_vol"] = (
        panel.groupby("asset")["long_ret"]
        .transform(lambda x: x.rolling(vol_window, min_periods=max(10, vol_window // 3)).std(ddof=0) * np.sqrt(TRADING_DAYS))
        .bfill()
        .fillna(0.0)
    )
    panel["carry_quality"] = 0.0
    panel["model_panel"] = "price_only"
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
        "hedge_ratio": float(s_cfg.get("hedge_ratio", 1.0)),
        "position_direction": int(float(s_cfg.get("position_direction", 1))),
        "trend_floor": float(s_cfg.get("trend_floor", -10.0)),
        "use_trend_filter": bool(s_cfg.get("use_trend_filter", False)),
        "strategy_mode": str(s_cfg.get("strategy_mode", "carry")),
        "relative_momentum_window": int(float(s_cfg.get("relative_momentum_window", 63))),
        "relative_entry": float(s_cfg.get("relative_entry", 0.0)),
        "relative_short_ratio": float(s_cfg.get("relative_short_ratio", 0.5)),
        "market_momentum_window": int(float(s_cfg.get("market_momentum_window", 21))),
        "bear_threshold": float(s_cfg.get("bear_threshold", -0.05)),
        "bear_mode": str(s_cfg.get("bear_mode", "weak_strong")),
        "bear_long_ratio": float(s_cfg.get("bear_long_ratio", 0.25)),
        "max_leverage": float(s_cfg.get("max_leverage", 1.0)),
    }


def _cfg_with_params(cfg: dict, params: dict) -> dict:
    out = deepcopy(cfg)
    out["strategy"] = dict(out["strategy"])
    for key, value in params.items():
        out["strategy"][key] = value
    return out


def compute_positions(panel: pd.DataFrame, cfg: dict, params: dict | None = None) -> pd.DataFrame:
    p = _params_from_cfg(_cfg_with_params(cfg, params or {}))
    mode = str(p.get("strategy_mode", "carry")).lower()
    if mode in {"etf_cross_momentum", "etf_regime_relative_momentum"}:
        return compute_etf_cross_momentum_positions(panel, cfg, p)

    entry = float(p["min_basis_entry"])
    exit_basis = float(p["exit_basis"])
    basis_z_entry = float(p["basis_z_entry"])
    max_asset_weight = float(p["max_asset_weight"])
    gross_target = float(p["gross_target"])
    vol_target = float(p["vol_target"])
    max_pair_vol = float(p["max_pair_vol"])
    direction = 1 if float(p.get("position_direction", 1)) >= 0 else -1
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
            raw_weight = direction * min(max_asset_weight, quality) if active else 0.0
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
    gross = pos["target_weight"].abs().groupby(pos["date"]).transform("sum").replace(0, np.nan)
    scaler = (gross_target / gross).clip(upper=1.0).fillna(0.0)
    pos["target_weight"] = (pos["target_weight"] * scaler).clip(-max_asset_weight, max_asset_weight)
    return pos


def compute_etf_cross_momentum_positions(panel: pd.DataFrame, cfg: dict, p: dict) -> pd.DataFrame:
    momentum_window = int(p.get("relative_momentum_window", 63))
    entry = float(p.get("relative_entry", 0.0))
    short_ratio = max(0.0, float(p.get("relative_short_ratio", 0.5)))
    mode = str(p.get("strategy_mode", "etf_cross_momentum")).lower()
    gross_target = float(p["gross_target"])
    vol_target = float(p["vol_target"])
    max_leverage = float(p.get("max_leverage", 1.0))
    max_asset_weight = float(p["max_asset_weight"])
    vol_window = int(cfg["strategy"].get("vol_window", 21))

    pos = panel.sort_values(["asset", "date"]).copy()
    close = pos.pivot(index="date", columns="asset", values="long_close").sort_index()
    rets = close.pct_change().fillna(0.0)
    momentum = close.pct_change(momentum_window).replace([np.inf, -np.inf], np.nan)
    weights = pd.DataFrame(0.0, index=close.index, columns=close.columns)

    if {"BTC", "ETH"}.issubset(set(close.columns)):
        spread = momentum["ETH"] - momentum["BTC"]
        if mode == "etf_regime_relative_momentum":
            market_momentum_window = int(p.get("market_momentum_window", 21))
            bear_threshold = float(p.get("bear_threshold", -0.05))
            bear_mode = str(p.get("bear_mode", "weak_strong")).lower()
            bear_long_ratio = max(0.0, float(p.get("bear_long_ratio", 0.25)))
            market_momentum = close.mean(axis=1).pct_change(market_momentum_window)
            bear = market_momentum < bear_threshold
            eth_weak = bear & (momentum["ETH"] < momentum["BTC"])
            btc_weak = bear & (momentum["BTC"] <= momentum["ETH"])
            if bear_mode == "both":
                weights.loc[bear, ["BTC", "ETH"]] = -0.5
            elif bear_mode == "weak":
                weights.loc[eth_weak, "ETH"] = -1.0
                weights.loc[btc_weak, "BTC"] = -1.0
            else:
                weights.loc[eth_weak, "ETH"] = -1.0
                weights.loc[eth_weak, "BTC"] = bear_long_ratio
                weights.loc[btc_weak, "BTC"] = -1.0
                weights.loc[btc_weak, "ETH"] = bear_long_ratio
            trade_relative = ~bear.fillna(False)
        else:
            trade_relative = pd.Series(True, index=close.index)
        long_eth = trade_relative & (spread > entry)
        long_btc = trade_relative & (spread < -entry)
        weights.loc[long_eth, "ETH"] = 1.0
        weights.loc[long_eth, "BTC"] = -short_ratio
        weights.loc[long_btc, "BTC"] = 1.0
        weights.loc[long_btc, "ETH"] = -short_ratio
    else:
        ranks = momentum.rank(axis=1, ascending=False, method="first")
        weights = (ranks == 1).astype(float)

    gross = weights.abs().sum(axis=1).replace(0, np.nan)
    base_weights = weights.div(gross, axis=0).fillna(0.0) * gross_target
    base_ret = (base_weights.shift(1).fillna(0.0) * rets).sum(axis=1)
    realized_vol = base_ret.rolling(vol_window, min_periods=max(10, vol_window // 2)).std(ddof=0) * np.sqrt(TRADING_DAYS)
    leverage = (vol_target / realized_vol.replace(0, np.nan)).clip(lower=0.0, upper=max_leverage).fillna(0.0)
    final_weights = base_weights.mul(leverage.shift(1).fillna(0.0), axis=0).clip(-max_asset_weight, max_asset_weight)

    long_form = final_weights.stack().rename("target_weight").reset_index()
    long_form = long_form.rename(columns={"level_1": "asset"})
    out = pos.merge(long_form, on=["date", "asset"], how="left")
    out["target_weight"] = out["target_weight"].fillna(0.0)
    out["active"] = out["target_weight"].abs() > 0
    out["entry_ok"] = out["active"]
    out["basis_z_ok"] = True
    out["vol_ok"] = True
    out["trend_ok"] = True
    out["raw_weight"] = out["target_weight"]
    out["vol_scale"] = leverage.reindex(out["date"]).to_numpy()
    out["strategy_mode"] = mode
    return out.sort_values(["date", "asset"])


def simulate(panel: pd.DataFrame, cfg: dict, params: dict | None = None) -> tuple[pd.Series, pd.DataFrame]:
    effective_cfg = _cfg_with_params(cfg, params or {})
    pos = compute_positions(panel, effective_cfg)
    s_cfg = effective_cfg["strategy"]
    mode = str(s_cfg.get("strategy_mode", "carry")).lower()
    carry_cost = 0.0 if mode.startswith("etf_") else float(s_cfg["carry_cost_bps"]) / 10000.0 / TRADING_DAYS
    financing = float(s_cfg["financing_bps"]) / 10000.0 / TRADING_DAYS
    futures_cost_bps = 0.0 if mode.startswith("etf_") else float(s_cfg["futures_roundtrip_bps"])
    roundtrip = (futures_cost_bps + float(s_cfg["long_leg_roundtrip_bps"])) / 10000.0
    hedge_ratio = float(s_cfg.get("hedge_ratio", 1.0))

    pos = pos.sort_values(["asset", "date"])
    pos["prev_weight"] = pos.groupby("asset")["target_weight"].shift(1).fillna(0.0)
    pos["turnover"] = (pos["target_weight"] - pos["prev_weight"]).abs()
    if mode.startswith("etf_"):
        financed_gross = pos["prev_weight"].abs().groupby(pos["date"]).transform("sum").sub(1.0).clip(lower=0.0)
        short_gross = pos["prev_weight"].clip(upper=0.0).abs()
        pos["gross_daily_cost"] = (financed_gross + short_gross) * financing
    else:
        pos["gross_daily_cost"] = pos["prev_weight"].abs() * (carry_cost + financing)
    pos["trading_cost"] = pos["turnover"] * roundtrip
    pos["strategy_ret"] = (
        pos["prev_weight"] * (pos["long_ret"] - hedge_ratio * pos["future_ret"])
        - pos["gross_daily_cost"] * (1.0 + hedge_ratio)
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
        row["active_days"] = int((positions["target_weight"].abs().groupby(positions["date"]).sum() > 0).sum()) if not positions.empty else 0
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
    out.loc[out["active_days"].astype(float) <= 0, "selection_score"] = -1e9
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
    mode = str(cfg["strategy"].get("strategy_mode", "carry")).lower()
    panel = build_price_panel(long_prices, cfg) if mode.startswith("etf_") else build_signal_panel(curve, long_prices, cfg)
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
