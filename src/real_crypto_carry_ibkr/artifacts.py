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
    "manifest.json",
}


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def latest_execution_plan(positions: pd.DataFrame, cfg: dict) -> list[dict[str, Any]]:
    if positions.empty:
        return []
    latest_date = positions["date"].max()
    latest = positions[positions["date"] == latest_date].copy()
    rows: list[dict[str, Any]] = []
    capital = float(cfg["strategy"]["capital_usd"])
    for _, row in latest.iterrows():
        asset = str(row["asset"])
        asset_cfg = cfg["assets"].get(asset, {})
        weight = float(row.get("target_weight", 0.0) or 0.0)
        if weight <= 0:
            continue
        notional = capital * weight
        future_px = float(row["future_settle"])
        long_px = float(row["long_close"])
        multiplier = float(asset_cfg.get("multiplier", 1.0))
        contracts = int(round(notional / max(future_px * multiplier, 1e-9)))
        shares = int(round(notional / max(long_px, 1e-9)))
        if contracts <= 0 or shares <= 0:
            continue
        rows.append(
            {
                "date": str(pd.Timestamp(latest_date).date()),
                "asset": asset,
                "long_leg": {
                    "venue": "IBKR",
                    "secType": "STK",
                    "symbol": str(row.get("long_symbol") or asset_cfg.get("long_symbol")),
                    "side": "BUY",
                    "quantity_estimate": shares,
                    "notional_usd": notional,
                },
                "future_leg": {
                    "venue": "IBKR",
                    "secType": "FUT",
                    "root": asset_cfg.get("default_future_root"),
                    "localSymbol": asset_cfg.get("ibkr_local_symbol", ""),
                    "conId": int(asset_cfg.get("ibkr_conid", 0) or 0),
                    "exchange": asset_cfg.get("ibkr_exchange", ""),
                    "side": "SELL",
                    "quantity_estimate": contracts,
                    "notional_usd": notional,
                    "contract_multiplier_coin": multiplier,
                },
                "basis_ann": float(row["basis_ann"]),
                "target_weight": weight,
            }
        )
    return rows


def evaluate_status(summary: pd.DataFrame, provenance: Any, execution_plan: list[dict[str, Any]], cfg: dict) -> dict[str, Any]:
    s_cfg = cfg["strategy"]
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
        "status": "DEPLOYABLE_IBKR_CARRY" if not failures else "RESEARCH_ONLY_NOT_DEPLOYABLE",
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
    execution_plan = latest_execution_plan(positions, cfg)
    status = evaluate_status(summary, provenance, execution_plan, cfg)

    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "strategy_name": "real_crypto_futures_carry_ibkr",
        "strategy_family": "regulated_crypto_futures_cash_and_carry",
        "version": "0.1.0",
        "data_provenance": provenance.__dict__,
        "curve_sha256": sha256_file(curve_path),
        "long_prices_sha256": sha256_file(prices_path),
        "deployment_status": status["status"],
        "deployment_reason": status["reason"],
        "warning": "No profit or Sharpe is guaranteed. Deployability is a data-and-OOS gate, not a prediction.",
    }

    write_json(out / "metadata.json", metadata)
    write_json(out / "status.json", status)
    write_json(out / "config.json", cfg)
    write_json(out / "execution_plan_latest.json", execution_plan)
    summary.to_csv(out / "performance_summary.csv", index=False)
    returns.rename("strategy_return").to_csv(out / "daily_returns.csv")
    panel.to_csv(out / "signals.csv", index=False)
    positions.to_csv(out / "positions.csv", index=False)
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
