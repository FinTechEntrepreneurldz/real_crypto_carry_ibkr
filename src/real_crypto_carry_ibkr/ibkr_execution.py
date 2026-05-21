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
DONE_STATUSES = {"filled", "cancelled", "inactive", "apicancelled"}


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


def _positive_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except Exception:
        return float(default)
    return out if out > 0 else float(default)


def leg_reference_price(leg: dict[str, Any], is_future: bool = False) -> float:
    ref = _positive_float(leg.get("reference_price"))
    if ref:
        return ref
    notional = _positive_float(leg.get("notional_usd"))
    qty = _positive_float(leg.get("quantity_estimate"))
    if not notional or not qty:
        return 0.0
    if is_future:
        multiplier = _positive_float(leg.get("contract_multiplier_coin"), 1.0)
        return notional / max(qty * multiplier, 1e-9)
    return notional / qty


def signed_qty(side: str, qty: int | float) -> int:
    q = abs(int(round(float(qty))))
    return q if str(side).upper() == "BUY" else -q


def side_from_signed_qty(qty: int | float) -> str:
    return "BUY" if float(qty) > 0 else "SELL"


def summary_key(tag: str) -> str:
    out = []
    for i, ch in enumerate(str(tag or "")):
        if ch.isupper() and i > 0:
            out.append("_")
        out.append(ch.lower())
    return "".join(out).replace("__", "_").strip("_")


def execution_plan_order(row: dict[str, Any]) -> tuple[int, str]:
    long_leg = row.get("long_leg") or {}
    side = str(long_leg.get("side") or "").upper()
    # Submit SELL/reducing legs before BUY legs so margin can refresh before adding exposure.
    side_rank = 0 if side == "SELL" else 1
    return side_rank, str(row.get("asset") or "")


def resolve_managed_account(requested_account: str, managed_accounts: list[str], auto_select: bool = True) -> tuple[str, bool]:
    requested = str(requested_account or "").strip()
    managed = [str(account or "").strip() for account in managed_accounts if str(account or "").strip()]
    if requested in managed:
        return requested, False

    paper_accounts = [account for account in managed if account.upper().startswith("DU")]
    if auto_select and len(paper_accounts) == 1:
        return paper_accounts[0], True

    masked_requested = requested[:2] + "***" + requested[-2:] if len(requested) >= 4 else requested
    masked_managed = [account[:2] + "***" + account[-2:] if len(account) >= 4 else account for account in managed]
    raise RuntimeError(
        f"Connected to IBKR, but {masked_requested} is not in managed accounts: {masked_managed}. "
        "Update the IBKR_ACCOUNT secret or set IBKR_AUTO_SELECT_MANAGED_ACCOUNT=true when the runner is logged into exactly one paper account."
    )


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
        if env_bool("IBKR_REQUIRE_PAPER_TRADING", True):
            if self.conn.port not in PAPER_PORTS:
                raise RuntimeError("Paper-only mode permits only TWS 7497 or IB Gateway 4002.")
            if not account.upper().startswith("DU"):
                raise RuntimeError("Paper-only mode requires an IBKR paper account id that starts with DU.")
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

    def account_summary(self) -> dict[str, Any]:
        account = os.environ.get("IBKR_ACCOUNT", "").strip()
        try:
            rows = self.ib.accountSummary(account=account or "")
        except TypeError:
            rows = self.ib.accountSummary()
        out: dict[str, Any] = {"account": account}
        for row in rows:
            if account and str(getattr(row, "account", "")) != account:
                continue
            tag = str(getattr(row, "tag", "") or "")
            key = summary_key(tag)
            value = getattr(row, "value", "")
            try:
                parsed: Any = float(value)
            except Exception:
                parsed = value
            out[key] = parsed
            if tag == "NetLiquidation":
                out["net_liquidation"] = parsed
                out["currency"] = getattr(row, "currency", "") or "USD"
        if "net_liquidation" not in out:
            raise RuntimeError("IBKR account summary did not include NetLiquidation.")
        return out

    def make_order(self, contract: Contract, side: str, qty: int, is_future: bool, fallback_ref: float = 0.0):
        account = os.environ.get("IBKR_ACCOUNT", "").strip()
        order_type = os.environ.get("IBKR_FUTURES_ORDER_TYPE" if is_future else "IBKR_LONG_LEG_ORDER_TYPE", "LIMIT").upper()
        tif = os.environ.get("IBKR_ORDER_TIF", "DAY").strip().upper() or "DAY"
        if order_type in {"MKT", "MARKET"}:
            order = MarketOrder(side, abs(int(qty)))
            order.tif = tif
            order.outsideRth = env_bool("IBKR_OUTSIDE_RTH", False)
            if account:
                order.account = account
            return order, "market"

        q = self.quote(contract)
        ref = q["ask"] if side.upper() == "BUY" else q["bid"]
        ref = ref or q["market"] or q["last"] or q["close"]
        used_fallback = False
        if not ref:
            if not env_bool("IBKR_ALLOW_HISTORICAL_LIMIT_FALLBACK", True):
                raise RuntimeError(f"No usable quote for {contract}")
            ref = _positive_float(fallback_ref)
            used_fallback = bool(ref)
        if not ref:
            raise RuntimeError(f"No usable quote or artifact reference price for {contract}")
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
        order.tif = tif
        order.outsideRth = env_bool("IBKR_OUTSIDE_RTH", False)
        if account:
            order.account = account
        fallback_diag = f";fallback_ref={ref}" if used_fallback else ""
        return order, f"limit={limit_px};tif={tif};outsideRth={order.outsideRth};quote={q}{fallback_diag}"

    def trade_status(self, trade, existing: bool = False) -> dict[str, Any]:
        return {
            "status": str(trade.orderStatus.status or ""),
            "order_id": str(trade.order.orderId),
            "filled_qty": float(trade.orderStatus.filled or 0),
            "remaining_qty": float(trade.orderStatus.remaining or 0),
            "avg_fill_price": float(trade.orderStatus.avgFillPrice or 0),
            "existing_order": bool(existing),
        }

    def find_matching_open_trade(self, contract: Contract, side: str, qty: int):
        try:
            self.ib.reqOpenOrders()
            self.ib.sleep(env_float("IBKR_OPEN_ORDERS_SLEEP_SECONDS", 1.0))
        except Exception:
            pass
        expected_qty = abs(int(qty))
        expected_side = side.upper()
        expected_con_id = int(getattr(contract, "conId", 0) or 0)
        for trade in self.ib.openTrades():
            status = str(trade.orderStatus.status or "").lower()
            if status in DONE_STATUSES:
                continue
            order = trade.order
            open_contract = trade.contract
            open_con_id = int(getattr(open_contract, "conId", 0) or 0)
            if expected_con_id and open_con_id and open_con_id != expected_con_id:
                continue
            if str(order.action or "").upper() != expected_side:
                continue
            if abs(int(float(order.totalQuantity or 0))) != expected_qty:
                continue
            return trade
        return None

    def current_position_qty(self, contract: Contract) -> int:
        account = os.environ.get("IBKR_ACCOUNT", "").strip()
        expected_con_id = int(getattr(contract, "conId", 0) or 0)
        try:
            positions = self.ib.positions()
        except Exception:
            positions = []
        total = 0.0
        for pos in positions:
            if account and str(pos.account) != account:
                continue
            con_id = int(getattr(pos.contract, "conId", 0) or 0)
            if expected_con_id and con_id == expected_con_id:
                total += float(pos.position or 0)
        return int(round(total))

    def open_order_signed_qty(self, contract: Contract) -> int:
        expected_con_id = int(getattr(contract, "conId", 0) or 0)
        total = 0.0
        try:
            self.ib.reqAllOpenOrders()
            self.ib.sleep(env_float("IBKR_OPEN_ORDERS_SLEEP_SECONDS", 1.0))
        except Exception:
            try:
                self.ib.reqOpenOrders()
                self.ib.sleep(env_float("IBKR_OPEN_ORDERS_SLEEP_SECONDS", 1.0))
            except Exception:
                pass
        for trade in self.ib.openTrades():
            status = str(trade.orderStatus.status or "").lower()
            if status in DONE_STATUSES:
                continue
            open_con_id = int(getattr(trade.contract, "conId", 0) or 0)
            if expected_con_id and open_con_id and open_con_id != expected_con_id:
                continue
            remaining = float(trade.orderStatus.remaining or 0)
            if remaining <= 0:
                remaining = float(trade.order.totalQuantity or 0) - float(trade.orderStatus.filled or 0)
            if remaining <= 0:
                continue
            total += signed_qty(str(trade.order.action or ""), remaining)
        return int(round(total))

    def rebalance_delta(self, contract: Contract, leg: dict[str, Any]) -> dict[str, Any]:
        target = signed_qty(str(leg["side"]), int(leg["quantity_estimate"]))
        current = self.current_position_qty(contract)
        open_qty = self.open_order_signed_qty(contract)
        delta = target - current - open_qty
        return {
            "target_qty": int(target),
            "current_qty": int(current),
            "open_order_qty": int(open_qty),
            "delta_qty": int(delta),
            "side": side_from_signed_qty(delta) if delta else str(leg["side"]).upper(),
            "quantity": abs(int(delta)),
        }

    def wait_trade(self, trade, wait_seconds: float) -> dict[str, Any]:
        end = time.time() + wait_seconds
        while time.time() < end:
            self.ib.sleep(1.0)
            status = str(trade.orderStatus.status or "")
            remaining = float(trade.orderStatus.remaining or 0)
            if status.lower() in {"filled", "cancelled", "inactive"} or remaining <= 0:
                break
        return self.trade_status(trade)

    def execute_plan(self, execution_plan: list[dict[str, Any]], dry_run: bool = True) -> list[dict[str, Any]]:
        if not dry_run:
            self.assert_submit_allowed()
        results = []
        cap = env_int("IBKR_MAX_FUTURES_CONTRACTS_PER_RUN", 1)
        wait_seconds = env_float("IBKR_ORDER_WAIT_SECONDS", 45.0)
        cancel_unfilled = env_bool("IBKR_CANCEL_UNFILLED", True)
        between_order_sleep = env_float("IBKR_BETWEEN_ORDER_SLEEP_SECONDS", 2.0)
        ordered_plan = sorted(execution_plan, key=execution_plan_order)

        for row_index, row in enumerate(ordered_plan):
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
                fut_delta = self.rebalance_delta(future, {**future_leg, "quantity_estimate": fut_qty})
                if fut_delta["quantity"] <= 0:
                    fut_stat = {"filled_qty": 0.0}
                    item["legs"].append({"leg": "future", "diag": "already_at_target_or_open", **fut_delta})
                    results.append(item)
                    continue
                fut_order, fut_diag = self.make_order(
                    future,
                    fut_delta["side"],
                    fut_delta["quantity"],
                    is_future=True,
                    fallback_ref=leg_reference_price(future_leg, is_future=True),
                )
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
                adj_target_qty = max(1, int(round(long_qty * fill_ratio)))
                long_delta = self.rebalance_delta(stock, {**long_leg, "quantity_estimate": adj_target_qty})
                if long_delta["quantity"] <= 0:
                    item["legs"].append({"leg": "long", "diag": "already_at_target_or_open", **long_delta})
                    results.append(item)
                    continue
                stock_order, stock_diag = self.make_order(
                    stock,
                    long_delta["side"],
                    long_delta["quantity"],
                    is_future=False,
                    fallback_ref=leg_reference_price(long_leg),
                )
                stock_trade = self.ib.placeOrder(stock, stock_order)
                stock_stat = self.wait_trade(stock_trade, wait_seconds)
                item["legs"].append({"leg": "long", "diag": stock_diag, **stock_stat})
            results.append(item)
            if not dry_run and row_index < len(ordered_plan) - 1 and between_order_sleep > 0:
                self.ib.sleep(between_order_sleep)
        return results


def load_execution_plan(artifact_dir: str | Path) -> list[dict[str, Any]]:
    path = Path(artifact_dir) / "execution_plan_latest.json"
    return json.loads(path.read_text(encoding="utf-8"))
