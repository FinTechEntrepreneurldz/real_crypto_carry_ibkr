from __future__ import annotations

import hashlib
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .config import write_json


REQUIRED_ZIP_FILES = {
    "metadata.json",
    "status.json",
    "config.json",
    "execution_plan_latest.json",
    "performance_summary.csv",
    "daily_returns.csv",
    "signals.csv",
    "positions.csv",
    "grid_results.csv",
    "selected_params.json",
    "manifest.json",
}


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def execution_plan_order(row: dict[str, Any]) -> tuple[int, str]:
    long_leg = row.get("long_leg") or {}
    side = str(long_leg.get("side") or "").upper()
    # Submit SELL/reducing legs before BUY legs so margin can refresh before adding exposure.
    side_rank = 0 if side == "SELL" else 1
    return side_rank, str(row.get("asset") or "")


def latest_execution_plan(positions: pd.DataFrame, cfg: dict) -> list[dict[str, Any]]:
    if positions.empty:
        return []
    latest_date = positions["date"].max()
    latest = positions[positions["date"] == latest_date].copy()
    rows: list[dict[str, Any]] = []
    s_cfg = cfg["strategy"]
    capital = float(s_cfg["capital_usd"])
    gross_cap = s_cfg.get("execution_gross_cap")
    gross_cap = float(gross_cap) if gross_cap is not None else None
    raw_gross = float(latest["target_weight"].abs().sum())
    execution_scale = 1.0
    if gross_cap is not None and raw_gross > gross_cap > 0:
        execution_scale = gross_cap / raw_gross

    for _, row in latest.iterrows():
        asset = str(row["asset"])
        asset_cfg = cfg["assets"].get(asset, {})
        model_weight = float(row.get("target_weight", 0.0) or 0.0)
        execution_weight = model_weight * execution_scale
        if abs(execution_weight) <= 0:
            continue
        notional = capital * abs(execution_weight)
        long_px = float(row["long_close"])
        multiplier = float(asset_cfg.get("multiplier", 1.0))
        hedge_ratio = float(cfg["strategy"].get("hedge_ratio", 1.0))
        future_px = float(row["future_settle"]) if pd.notna(row.get("future_settle")) else 0.0
        contracts = 0
        if future_px > 0 and hedge_ratio > 0:
            contracts = int(round((notional * hedge_ratio) / max(future_px * multiplier, 1e-9)))
        shares = int(round(notional / max(long_px, 1e-9)))
        if shares <= 0:
            continue
        future_leg = None
        if contracts > 0:
            future_leg = {
                "venue": "IBKR",
                "secType": "FUT",
                "root": asset_cfg.get("default_future_root"),
                "localSymbol": asset_cfg.get("ibkr_local_symbol", ""),
                "conId": int(asset_cfg.get("ibkr_conid", 0) or 0),
                "exchange": asset_cfg.get("ibkr_exchange", ""),
                "side": "SELL" if execution_weight > 0 else "BUY",
                "quantity_estimate": contracts,
                "notional_usd": notional * hedge_ratio,
                "reference_price": future_px,
                "contract_multiplier_coin": multiplier,
                "hedge_ratio": hedge_ratio,
            }
        rows.append(
            {
                "date": str(pd.Timestamp(latest_date).date()),
                "asset": asset,
                "long_leg": {
                    "venue": "IBKR",
                    "secType": "STK",
                    "symbol": str(row.get("long_symbol") or asset_cfg.get("long_symbol")),
                    "side": "BUY" if execution_weight > 0 else "SELL",
                    "quantity_estimate": shares,
                    "notional_usd": notional,
                    "reference_price": long_px,
                },
                "future_leg": future_leg,
                "basis_ann": float(row["basis_ann"]),
                "target_weight": execution_weight,
                "model_target_weight": model_weight,
                "execution_scale": execution_scale,
                "capital_usd": capital,
                "execution_gross_cap": gross_cap,
            }
        )
    return sorted(rows, key=execution_plan_order)


def evaluate_status(summary: pd.DataFrame, provenance: Any, execution_plan: list[dict[str, Any]], cfg: dict) -> dict[str, Any]:
    s_cfg = cfg["strategy"]
    strategy_mode = str(s_cfg.get("strategy_mode", "carry")).lower()
    deployable_status = "DEPLOYABLE_IBKR_ETF_REGIME" if strategy_mode.startswith("etf_") else "DEPLOYABLE_IBKR_CARRY"
    test = summary[summary["period"] == "test"]
    if test.empty:
        return {"status": "RESEARCH_ONLY_NOT_DEPLOYABLE", "reason": "missing test period"}
    t = test.iloc[0].to_dict()
    failures = []
    if not bool(provenance.accepted):
        failures.append(provenance.reason)
    if int(t.get("n", 0)) < int(s_cfg["min_test_days"]):
        failures.append("insufficient test observations")
    if float(t.get("sharpe", 0.0)) < float(s_cfg["target_sharpe"]):
        failures.append("test Sharpe below target")
    if float(t.get("ann_return", 0.0)) < float(s_cfg["target_ann_return"]):
        failures.append("test annual return below target")
    if float(t.get("max_dd", 0.0)) < float(s_cfg["max_test_drawdown"]):
        failures.append("test max drawdown worse than limit")
    if not execution_plan:
        failures.append("latest execution plan is empty")
    return {
        "status": deployable_status if not failures else "RESEARCH_ONLY_NOT_DEPLOYABLE",
        "reason": "passed real-data OOS deployment gates" if not failures else "; ".join(failures),
        "test": t,
        "target_sharpe": float(s_cfg["target_sharpe"]),
        "target_ann_return": float(s_cfg["target_ann_return"]),
        "max_test_drawdown": float(s_cfg["max_test_drawdown"]),
    }


def write_artifacts(
    out_dir: str | Path,
    cfg: dict,
    provenance: Any,
    research: dict[str, Any],
    curve_path: str | Path,
    prices_path: str | Path,
) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary = research["summary"]
    returns = research["returns"]
    panel = research["panel"]
    positions = research["positions"]
    grid_results = research.get("grid_results", pd.DataFrame())
    selected_params = research.get("selected_params", {})
    effective_cfg = research.get("effective_config", cfg)
    execution_plan = latest_execution_plan(positions, effective_cfg)
    status = evaluate_status(summary, provenance, execution_plan, effective_cfg)
    strategy_mode = str(effective_cfg["strategy"].get("strategy_mode", "carry"))

    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "strategy_name": "real_crypto_regime_ibkr",
        "strategy_family": "regulated_crypto_etf_relative_momentum" if strategy_mode.startswith("etf_") else "regulated_crypto_futures_cash_and_carry",
        "version": "0.2.0",
        "data_provenance": provenance.__dict__,
        "curve_sha256": sha256_file(curve_path),
        "long_prices_sha256": sha256_file(prices_path),
        "model_selection": {
            "enabled": bool(cfg["strategy"].get("model_selection", {}).get("enabled", False)),
            "selected_params": selected_params,
            "grid_trials": int(len(grid_results)) if grid_results is not None else 0,
        },
        "deployment_status": status["status"],
        "deployment_reason": status["reason"],
        "warning": (
            "No profit or Sharpe is guaranteed. Deployability is a data-and-OOS gate, not a prediction. "
            "ETF regime artifacts can involve leveraged long/short ETF exposure and must be paper traded before live use."
        ),
    }

    write_json(out / "metadata.json", metadata)
    write_json(out / "status.json", status)
    write_json(out / "config.json", effective_cfg)
    write_json(out / "execution_plan_latest.json", execution_plan)
    write_json(out / "selected_params.json", selected_params)
    summary.to_csv(out / "performance_summary.csv", index=False)
    returns.rename("strategy_return").to_csv(out / "daily_returns.csv")
    panel.to_csv(out / "signals.csv", index=False)
    positions.to_csv(out / "positions.csv", index=False)
    if grid_results is None or grid_results.empty:
        pd.DataFrame([selected_params]).to_csv(out / "grid_results.csv", index=False)
    else:
        grid_results.sort_values(
            [col for col in ["selection_score", "validation_sharpe", "test_sharpe"] if col in grid_results.columns],
            ascending=False,
        ).to_csv(out / "grid_results.csv", index=False)
    manifest = {
        "files": sorted(REQUIRED_ZIP_FILES - {"manifest.json"}),
        "required_files": sorted(REQUIRED_ZIP_FILES),
        "created_at_utc": metadata["created_at_utc"],
    }
    write_json(out / "manifest.json", manifest)

    zip_path = out / "artifacts.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(REQUIRED_ZIP_FILES):
            zf.write(out / name, name)
    with zipfile.ZipFile(zip_path) as zf:
        missing = REQUIRED_ZIP_FILES - set(zf.namelist())
    if missing:
        raise RuntimeError(f"artifact zip is missing required files: {sorted(missing)}")
    return zip_path
