#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import argparse
from pathlib import Path

from real_crypto_carry_ibkr.dashboard_logs import mask_account
from real_crypto_carry_ibkr.ibkr_execution import IBKRCarryExecutor, IBKRConfig, env_bool, env_float, env_int, resolve_managed_account


def main() -> None:
    p = argparse.ArgumentParser(description="Verify IBKR paper connectivity and account NLV.")
    p.add_argument("--output-json", default=None, help="Optional path for the raw account summary JSON.")
    args = p.parse_args()

    account = os.environ.get("IBKR_ACCOUNT", "").strip()
    requested_account = account
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
        try:
            resolved_account, auto_selected = resolve_managed_account(
                account,
                managed_accounts,
                auto_select=env_bool("IBKR_AUTO_SELECT_MANAGED_ACCOUNT", True),
            )
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc
        if auto_selected:
            print(
                f"Requested IBKR_ACCOUNT={mask_account(account)} is not managed by this session; "
                f"using the single managed paper account {mask_account(resolved_account)}."
            )
        account = resolved_account
        os.environ["IBKR_ACCOUNT"] = resolved_account
        summary = executor.account_summary()
        summary["requested_account_masked"] = mask_account(requested_account)
        summary["account_auto_selected"] = auto_selected

    if args.output_json:
        path = Path(args.output_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    print(
        json.dumps(
            {
                "status": "ok",
                "account": mask_account(account),
                "host": conn.host,
                "port": conn.port,
                "net_liquidation": summary.get("net_liquidation"),
                "buying_power": summary.get("buying_power"),
                "available_funds": summary.get("available_funds"),
                "excess_liquidity": summary.get("excess_liquidity"),
                "currency": summary.get("currency"),
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
