#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os

from real_crypto_carry_ibkr.config import read_json
from real_crypto_carry_ibkr.ibkr_execution import IBKRCarryExecutor, IBKRConfig, load_execution_plan, env_bool, env_float, env_int


def main() -> None:
    p = argparse.ArgumentParser(description="Run IBKR rebalance from a vetted artifact.")
    p.add_argument("--artifact-dir", default="artifacts/latest")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    status = read_json(f"{args.artifact_dir}/status.json")
    deployable_statuses = {"DEPLOYABLE_IBKR_CARRY", "DEPLOYABLE_IBKR_ETF_REGIME"}
    if status.get("status") not in deployable_statuses:
        raise RuntimeError(f"Artifact is not deployable: {status.get('status')} | {status.get('reason')}")

    dry_run = args.dry_run or not env_bool("IBKR_SUBMIT_ORDERS", False)
    plan = load_execution_plan(args.artifact_dir)
    if dry_run:
        results = []
        for row in plan:
            item = {"asset": row["asset"], "dry_run": True, "legs": []}
            future_leg = row.get("future_leg")
            if future_leg:
                item["legs"].append(
                    {
                        "leg": "future",
                        "side": future_leg["side"],
                        "qty": int(future_leg["quantity_estimate"]),
                        "contract": future_leg.get("localSymbol") or future_leg.get("root"),
                    }
                )
            long_leg = row["long_leg"]
            item["legs"].append(
                {
                    "leg": "long",
                    "side": long_leg["side"],
                    "qty": int(long_leg["quantity_estimate"]),
                    "contract": long_leg["symbol"],
                }
            )
            results.append(item)
        print(json.dumps({"dry_run": True, "results": results}, indent=2, default=str))
        return

    conn = IBKRConfig(
        host=os.environ.get("IBKR_HOST", "127.0.0.1"),
        port=env_int("IBKR_PORT", 7497),
        client_id=env_int("IBKR_CLIENT_ID", 17),
        timeout=env_float("IBKR_TIMEOUT", 12.0),
    )
    with IBKRCarryExecutor(conn) as executor:
        results = executor.execute_plan(plan, dry_run=dry_run)
    print(json.dumps({"dry_run": dry_run, "results": results}, indent=2, default=str))


if __name__ == "__main__":
    main()
