from __future__ import annotations

import numpy as np
import pandas as pd

from .metrics import TRADING_DAYS, period_summary, zscore


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
    panel["basis_z"] = panel.groupby("asset")["basis_ann"].transform(lambda x: zscore(x, 42)).fillna(0.0)
    panel["realized_pair_vol"] = (
        panel.groupby("asset")["pair_ret_raw"]
        .transform(lambda x: x.rolling(int(cfg["strategy"]["vol_window"]), min_periods=10).std(ddof=0) * np.sqrt(TRADING_DAYS))
        .bfill()
        .fillna(0.0)
    )
    return panel


def compute_positions(panel: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    s_cfg = cfg["strategy"]
    entry = float(s_cfg["min_basis_entry"])
    exit_basis = float(s_cfg["exit_basis"])
    max_asset_weight = float(s_cfg["max_asset_weight"])
    gross_target = float(s_cfg["gross_target"])
    vol_target = float(s_cfg["vol_target"])

    out = []
    for asset, g in panel.groupby("asset", sort=True):
        active = False
        rows = []
        for _, row in g.sort_values("date").iterrows():
            b = float(row["basis_ann"])
            if not active and b >= entry:
                active = True
            elif active and b <= exit_basis:
                active = False

            raw_weight = max_asset_weight if active else 0.0
            vol = float(row.get("realized_pair_vol", 0.0) or 0.0)
            vol_scale = min(1.0, vol_target / vol) if vol > 0 else 0.0
            rows.append({**row.to_dict(), "active": bool(active), "raw_weight": raw_weight, "vol_scale": vol_scale})
        out.extend(rows)

    pos = pd.DataFrame(out).sort_values(["date", "asset"])
    pos["target_weight"] = pos["raw_weight"] * pos["vol_scale"]
    gross = pos.groupby("date")["target_weight"].transform("sum").replace(0, np.nan)
    scaler = (gross_target / gross).clip(upper=1.0).fillna(0.0)
    pos["target_weight"] = (pos["target_weight"] * scaler).clip(0.0, max_asset_weight)
    return pos


def simulate(panel: pd.DataFrame, cfg: dict) -> tuple[pd.Series, pd.DataFrame]:
    pos = compute_positions(panel, cfg)
    s_cfg = cfg["strategy"]
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


def run_research(curve: pd.DataFrame, long_prices: pd.DataFrame, cfg: dict) -> dict:
    panel = build_signal_panel(curve, long_prices, cfg)
    returns, positions = simulate(panel, cfg)
    summary = period_summary(returns, cfg)
    return {"panel": panel, "returns": returns, "positions": positions, "summary": summary}
