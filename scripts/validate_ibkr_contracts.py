#!/usr/bin/env python3
from __future__ import annotations

from real_crypto_carry_ibkr.config import load_config
from real_crypto_carry_ibkr.ibkr_execution import IBKRCarryExecutor, IBKRConfig, env_float, env_int


def main() -> None:
    cfg = load_config()
    conn = IBKRConfig(port=env_int("IBKR_PORT", 7497), timeout=env_float("IBKR_TIMEOUT", 12.0))
    with IBKRCarryExecutor(conn) as executor:
        for asset, spec in cfg["assets"].items():
            stock = executor.stock_contract(spec["long_symbol"])
            fut = executor.future_contract(
                {
                    "conId": spec["ibkr_conid"],
                    "exchange": spec["ibkr_exchange"],
                    "localSymbol": spec["ibkr_local_symbol"],
                }
            )
            print(asset, {"stock": stock, "future": fut})


if __name__ == "__main__":
    main()
