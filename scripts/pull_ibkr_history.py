#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import os

import pandas as pd
from ib_insync import IB, Contract, Stock


def main() -> None:
    out = Path("data")
    out.mkdir(exist_ok=True)
    ib = IB()
    ib.connect(
        os.environ.get("IBKR_HOST", "127.0.0.1"),
        int(os.environ.get("IBKR_PORT", "7497")),
        clientId=int(os.environ.get("IBKR_HISTORY_CLIENT_ID", "92")),
        timeout=float(os.environ.get("IBKR_TIMEOUT", "15")),
    )
    try:
        specs = {
            "BTC": {
                "long": Stock("IBIT", "SMART", "USD"),
                "spot": Contract(secType="CRYPTO", symbol="BTC", exchange="PAXOS", currency="USD"),
                "future": Contract(conId=805366677, secType="FUT", exchange="COINDERIV", currency="USD"),
                "future_contract": "BIPZ30",
                "expiry": "2030-12-20",
                "multiplier": 0.01,
                "exchange": "COINDERIV",
            },
            "ETH": {
                "long": Stock("ETHA", "SMART", "USD"),
                "spot": Contract(secType="CRYPTO", symbol="ETH", exchange="PAXOS", currency="USD"),
                "future": Contract(conId=805366682, secType="FUT", exchange="COINDERIV", currency="USD"),
                "future_contract": "ETPZ30",
                "expiry": "2030-12-20",
                "multiplier": 0.10,
                "exchange": "COINDERIV",
            },
        }
        curve_rows = []
        price_rows = []
        for asset, spec in specs.items():
            long = ib.qualifyContracts(spec["long"])[0]
            spot = ib.qualifyContracts(spec["spot"])[0]
            future = ib.qualifyContracts(spec["future"])[0]
            requests = [
                ("long", long, "TRADES", long.symbol),
                ("spot", spot, "AGGTRADES", asset),
                ("future", future, "TRADES", spec["future_contract"]),
            ]
            for label, contract, what_to_show, symbol in requests:
                bars = ib.reqHistoricalData(
                    contract,
                    endDateTime="",
                    durationStr="1 Y",
                    barSizeSetting="1 day",
                    whatToShow=what_to_show,
                    useRTH=False,
                    formatDate=1,
                    keepUpToDate=False,
                )
                print(asset, label, what_to_show, "bars", len(bars))
                for bar in bars:
                    date = pd.Timestamp(bar.date).date().isoformat()
                    close = float(bar.close)
                    if close <= 0:
                        continue
                    if label == "future":
                        curve_rows.append(
                            {
                                "date": date,
                                "asset": asset,
                                "contract": spec["future_contract"],
                                "expiry": spec["expiry"],
                                "settle": close,
                                "exchange": spec["exchange"],
                                "multiplier": spec["multiplier"],
                                "source": "ibkr_historical",
                            }
                        )
                    else:
                        price_rows.append(
                            {
                                "date": date,
                                "asset": asset,
                                "symbol": symbol,
                                "close": close,
                                "price_role": label,
                            }
                        )
        curve = pd.DataFrame(curve_rows).sort_values(["asset", "date"])
        prices = pd.DataFrame(price_rows).sort_values(["asset", "date", "price_role"])
        curve.to_csv(out / "cme_curve.csv", index=False)
        prices.to_csv(out / "long_prices.csv", index=False)
        print("wrote", out / "cme_curve.csv", curve.shape)
        print("wrote", out / "long_prices.csv", prices.shape)
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
