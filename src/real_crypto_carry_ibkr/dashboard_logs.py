from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PORTFOLIO_COLUMNS = [
    "timestamp_utc",
    "portfolio_value",
    "net_liquidation",
    "currency",
    "account_status",
    "source",
    "action",
    "submit_orders",
]

DECISION_COLUMNS = [
    "market_date",
    "variant",
    "action",
    "submit_orders",
    "account_status",
    "account_value",
    "net_liquidation",
    "n_orders_submitted",
    "timestamp_utc",
]


def mask_account(account: str | None) -> str:
    value = str(account or "").strip()
    if len(value) <= 4:
        return value
    return f"{value[:2]}***{value[-2:]}"


def count_order_submissions(results: list[dict[str, Any]]) -> int:
    count = 0
    for row in results:
        for leg in row.get("legs", []):
            if leg.get("diag") == "already_at_target_or_open":
                continue
            status = str(leg.get("status") or "").lower()
            if status and status not in {"cancelled", "inactive", "apicancelled"}:
                count += 1
    return count


def append_csv_row(path: str | Path, row: dict[str, Any], columns: list[str]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    exists = p.exists() and p.stat().st_size > 0
    with p.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        if not exists:
            writer.writeheader()
        writer.writerow({col: row.get(col, "") for col in columns})


def write_single_csv_row(path: str | Path, row: dict[str, Any], columns: list[str]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerow({col: row.get(col, "") for col in columns})


def write_dashboard_logs(
    log_dir: str | Path,
    account_summary: dict[str, Any],
    results: list[dict[str, Any]],
    submit_orders: bool,
) -> dict[str, Any]:
    net_liq = account_summary.get("net_liquidation")
    if net_liq is None:
        raise RuntimeError("Cannot write dashboard logs without IBKR NetLiquidation.")

    now = datetime.now(timezone.utc)
    timestamp = now.isoformat()
    currency = str(account_summary.get("currency") or "USD")
    order_count = count_order_submissions(results)
    action = "paper_rebalance"
    account_status = "connected"
    base = Path(log_dir)

    portfolio_row = {
        "timestamp_utc": timestamp,
        "portfolio_value": net_liq,
        "net_liquidation": net_liq,
        "currency": currency,
        "account_status": account_status,
        "source": "ibkr_account_summary_net_liquidation",
        "action": action,
        "submit_orders": str(bool(submit_orders)),
    }
    decision_row = {
        "market_date": now.date().isoformat(),
        "variant": "real_crypto_regime_ibkr",
        "action": action,
        "submit_orders": str(bool(submit_orders)),
        "account_status": account_status,
        "account_value": net_liq,
        "net_liquidation": net_liq,
        "n_orders_submitted": order_count,
        "timestamp_utc": timestamp,
    }
    health = {
        "overall_status": "connected",
        "account_status": account_status,
        "net_liquidation": net_liq,
        "currency": currency,
        "source": "ibkr_account_summary_net_liquidation",
        "account_id_masked": mask_account(account_summary.get("account")),
        "updated_at_utc": timestamp,
    }

    append_csv_row(base / "portfolio" / "portfolio.csv", portfolio_row, PORTFOLIO_COLUMNS)
    append_csv_row(base / "decisions" / "decisions.csv", decision_row, DECISION_COLUMNS)
    write_single_csv_row(base / "decisions" / "latest_decision.csv", decision_row, DECISION_COLUMNS)
    (base / "health").mkdir(parents=True, exist_ok=True)
    (base / "health" / "health_status.json").write_text(json.dumps(health, indent=2), encoding="utf-8")

    return {
        "portfolio_row": portfolio_row,
        "decision_row": decision_row,
        "health": health,
    }
