#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from real_crypto_carry_ibkr.dashboard_logs import mask_account, write_dashboard_logs


def main() -> None:
    parser = argparse.ArgumentParser(description="Write dashboard logs from a saved IBKR account summary.")
    parser.add_argument("--account-summary", required=True, help="Path to the IBKR account summary JSON.")
    parser.add_argument("--log-dir", default="logs", help="Dashboard log directory.")
    args = parser.parse_args()

    summary_path = Path(args.account_summary)
    account_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    dashboard_logs = write_dashboard_logs(
        args.log_dir,
        account_summary,
        results=[],
        submit_orders=False,
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "account": mask_account(account_summary.get("account")),
                "net_liquidation": account_summary.get("net_liquidation"),
                "dashboard_logs": dashboard_logs,
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
