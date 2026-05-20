#!/usr/bin/env python3
from __future__ import annotations

import json
import os

from real_crypto_carry_ibkr.dashboard_logs import mask_account
from real_crypto_carry_ibkr.ibkr_execution import IBKRCarryExecutor, IBKRConfig, env_float, env_int


def main() -> None:
    account = os.environ.get("IBKR_ACCOUNT", "").strip()
    port = env_int("IBKR_PORT", 7497)
    if not account:
        raise SystemExit("IBKR_ACCOUNT secret is required.")
    if not account.upper().startswith("DU"):
        raise SystemExit("IBKR_ACCOUNT must be a paper account id starting with DU.")
    if port not in {7497, 4002}:
        raise SystemExit("Paper rebalance requires TWS 7497 or IB Gateway 4002.")

    conn = IBKRConfig(
        host=os.environ.get("IBKR_HOST", "127.0.0.1"),
        port=port,
        client_id=env_int("IBKR_CLIENT_ID", 17),
        timeout=env_float("IBKR_TIMEOUT", 12.0),
    )
    with IBKRCarryExecutor(conn) as executor:
        managed_accounts = [str(a) for a in executor.ib.managedAccounts()]
        if account not in managed_accounts:
            raise SystemExit(
                f"Connected to IBKR, but {mask_account(account)} is not in managed accounts: "
                f"{[mask_account(a) for a in managed_accounts]}"
            )
        summary = executor.account_summary()

    print(
        json.dumps(
            {
                "status": "ok",
                "account": mask_account(account),
                "host": conn.host,
                "port": conn.port,
                "net_liquidation": summary.get("net_liquidation"),
                "currency": summary.get("currency"),
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
