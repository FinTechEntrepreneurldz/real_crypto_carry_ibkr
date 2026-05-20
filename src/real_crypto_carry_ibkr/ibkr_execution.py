from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from ib_insync import IB, Contract, LimitOrder, MarketOrder, Stock
except ImportError as exc:  # pragma: no cover - import guard for machines without IBKR stack
    raise ImportError("ib_insync is required for IBKR execution. Install with: pip install ib_insync") from exc


PAPER_PORTS = {7497, 4002}
LIVE_PORTS = {7496, 4001}
LIVE_ACK = "I_UNDERSTAND_THIS_SUBMITS_REAL_ORDERS"


def env_bool(name: str, default: bool = False) -> bool:
    return str(os.environ.get(name, str(default))).strip().lower() in {"1", "true", "yes", "y", "on"}


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return float(default)


def env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, default)))
    except Exception:
        return int(default)


def round_to_tick(px: float, tick: float, side: str) -> float:
    if tick <= 0:
        return float(px)
    q = px / tick
    rounded = math.ceil(q) * tick if side.upper() == "BUY" else math.floor(q) * tick
    return float(round(rounded, 10))


@dataclass
class IBKRConfig:
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 17
    timeout: float = 12.0


class IBKRCarryExecutor:
    def __init__(self, conn: IBKRConfig | None = None):
        self.conn = conn or IBKRConfig()
        self.ib = IB()

    def connect(self) -> None:
        self.ib.connect(self.conn.host, self.conn.port, clientId=self.conn.client_id, timeout=self.conn.timeout)

    def disconnect(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.disconnect()

    def assert_submit_allowed(self) -> None:
        account = os.environ.get("IBKR_ACCOUNT", "").strip()
        if not account:
            raise RuntimeError("IBKR_ACCOUNT must be set before submitting orders.")
        if env_bool("IBKR_REQUIRE_PAPER_TRADING", True) and self.conn.port not in PAPER_PORTS:
            raise RuntimeError("Paper-only mode permits only TWS 7497 or IB Gateway 4002.")
        if self.conn.port in LIVE_PORTS:
            if not env_bool("IBKR_ALLOW_LIVE_TRADING", False) or os.environ.get("IBKR_LIVE_TRADING_ACK", "") != LIVE_ACK:
                raise RuntimeError(f"Live trading requires IBKR_ALLOW_LIVE_TRADING=true and IBKR_LIVE_TRADING_ACK={LIVE_ACK}.")
        if not env_bool("IBKR_ALLOW_MARKET_ORDERS", False):
            for key in ["IBKR_FUTURES_ORDER_TYPE", "IBKR_LONG_LEG_ORDER_TYPE"]:
                if os.environ.get(key, "LIMIT").upper() in {"MKT", "MARKET"}:
                    raise RuntimeError(f"{key}=MKT is blocked unless IBKR_ALLOW_MARKET_ORDERS=true.")

    def stock_contract(self, symbol: str) -> Stock:
        contract = Stock(symbol, "SMART", "USD")
        qualified = self.ib.qualifyContracts(contract)
        if not qualified:
            raise RuntimeError(f"Could not qualify stock/ETF contract: {symbol}")
        return qualified[0]

    def future_contract(self, leg: dict[str, Any]) -> Contract:
        con_id = int(leg.get("conId", 0) or 0)
        if con_id <= 0:
            raise RuntimeError(f"Future leg has no conId: {leg}")
        contract = Contract(conId=con_id, secType="FUT", exchange=str(leg.get("exchange") or ""), currency="USD")
        qualified = self.ib.qualifyContracts(contract)
        if not qualified:
            raise RuntimeError(f"Could not qualify future contract: {leg}")
        return qualified[0]

    def quote(self, contract: Contract) -> dict[str, float]:
        try:
            self.ib.reqMarketDataType(3)
        except Exception:
            pass
        ticker = self.ib.reqMktData(contract, "", True, False)
        self.ib.sleep(env_float("IBKR_PRICE_SLEEP_SECONDS", 2.0))
        out = {
            "bid": float(ticker.bid) if ticker.bid and ticker.bid > 0 else 0.0,
            "ask": float(ticker.ask) if ticker.ask and ticker.ask > 0 else 0.0,
            "last": float(ticker.last) if ticker.last and ticker.last > 0 else 0.0,
            "close": float(ticker.close) if ticker.close and ticker.close > 0 else 0.0,
        }
        try:
            mp = ticker.marketPrice()
            out["market"] = float(mp) if mp and mp > 0 else 0.0
        except Exception:
            out["market"] = 0.0
        try:
            self.ib.cancelMktData(contract)
        except Exception:
            pass
        return out

    def make_order(self, contract: Contract, side: str, qty: int, is_future: bool):
        account = os.environ.get("IBKR_ACCOUNT", "").strip()
        order_type = os.environ.get("IBKR_FUTURES_ORDER_TYPE" if is_future else "IBKR_LONG_LEG_ORDER_TYPE", "LIMIT").upper()
        if order_type in {"MKT", "MARKET"}:
            order = MarketOrder(side, abs(int(qty)))
            if account:
                order.account = account
            return order, "market"

        q = self.quote(contract)
        ref = q["ask"] if side.upper() == "BUY" else q["bid"]
        ref = ref or q["market"] or q["last"] or q["close"]
        if not ref:
            raise RuntimeError(f"No usable quote for {contract}")
        if is_future:
            tick = env_float("IBKR_FUTURE_MIN_TICK", 0.5)
            offset = env_float("IBKR_LIMIT_OFFSET_TICKS", 2.0) * tick
            limit_px = ref + offset if side.upper() == "BUY" else ref - offset
            limit_px = round_to_tick(limit_px, tick, side)
        else:
            tick = env_float("IBKR_LONG_LEG_MIN_TICK", 0.01)
            offset_bps = env_float("IBKR_LONG_LEG_LIMIT_OFFSET_BPS", 5.0) / 10000.0
            limit_px = ref * (1.0 + offset_bps) if side.upper() == "BUY" else ref * (1.0 - offset_bps)
            limit_px = round_to_tick(limit_px, tick, side)
        order = LimitOrder(side, abs(int(qty)), float(limit_px))
        if account:
            order.account = account
        return order, f"limit={limit_px};quote={q}"

    def wait_trade(self, trade, wait_seconds: float) -> dict[str, Any]:
        end = time.time() + wait_seconds
        while time.time() < end:
            self.ib.sleep(1.0)
            status = str(trade.orderStatus.status or "")
            remaining = float(trade.orderStatus.remaining or 0)
            if status.lower() in {"filled", "cancelled", "inactive"} or remaining <= 0:
                break
        return {
            "status": str(trade.orderStatus.status or ""),
            "order_id": str(trade.order.orderId),
            "filled_qty": float(trade.orderStatus.filled or 0),
            "remaining_qty": float(trade.orderStatus.remaining or 0),
            "avg_fill_price": float(trade.orderStatus.avgFillPrice or 0),
        }

    def execute_plan(self, execution_plan: list[dict[str, Any]], dry_run: bool = True) -> list[dict[str, Any]]:
        if not dry_run:
            self.assert_submit_allowed()
        results = []
        cap = env_int("IBKR_MAX_FUTURES_CONTRACTS_PER_RUN", 1)
        wait_seconds = env_float("IBKR_ORDER_WAIT_SECONDS", 45.0)
        cancel_unfilled = env_bool("IBKR_CANCEL_UNFILLED", True)

        for row in execution_plan:
            future_leg = row["future_leg"]
            long_leg = row["long_leg"]
            fut_qty = 0 if not future_leg else min(abs(int(future_leg["quantity_estimate"])), cap) if cap > 0 else abs(int(future_leg["quantity_estimate"]))
            long_qty = abs(int(long_leg["quantity_estimate"]))
            if long_qty <= 0:
                continue
            future = self.future_contract(future_leg) if future_leg and fut_qty > 0 else None
            stock = self.stock_contract(long_leg["symbol"])

            item = {"asset": row["asset"], "dry_run": dry_run, "legs": []}
            if dry_run:
                if future_leg and future is not None:
                    item["legs"].append({"leg": "future", "side": future_leg["side"], "qty": fut_qty, "contract": str(future)})
                item["legs"].append({"leg": "long", "side": long_leg["side"], "qty": long_qty, "contract": str(stock)})
                results.append(item)
                continue

            fut_stat = {"filled_qty": 0.0}
            if future_leg and future is not None and fut_qty > 0:
                fut_order, fut_diag = self.make_order(future, future_leg["side"], fut_qty, is_future=True)
                fut_trade = self.ib.placeOrder(future, fut_order)
                fut_stat = self.wait_trade(fut_trade, wait_seconds)
                if cancel_unfilled and fut_stat["remaining_qty"] > 0:
                    self.ib.cancelOrder(fut_trade.order)
                    self.ib.sleep(1.0)
                item["legs"].append({"leg": "future", "diag": fut_diag, **fut_stat})

            if (not future_leg) or fut_stat["filled_qty"] > 0:
                fill_ratio = min(1.0, fut_stat["filled_qty"] / max(fut_qty, 1))
                if not future_leg:
                    fill_ratio = 1.0
                adj_long_qty = max(1, int(round(long_qty * fill_ratio)))
                stock_order, stock_diag = self.make_order(stock, long_leg["side"], adj_long_qty, is_future=False)
                stock_trade = self.ib.placeOrder(stock, stock_order)
                stock_stat = self.wait_trade(stock_trade, wait_seconds)
                item["legs"].append({"leg": "long", "diag": stock_diag, **stock_stat})
            results.append(item)
        return results


def load_execution_plan(artifact_dir: str | Path) -> list[dict[str, Any]]:
    path = Path(artifact_dir) / "execution_plan_latest.json"
    return json.loads(path.read_text(encoding="utf-8"))
