from ib_insync import Stock

from real_crypto_carry_ibkr.dashboard_logs import count_order_submissions, mask_account
from real_crypto_carry_ibkr.ibkr_execution import IBKRCarryExecutor, leg_reference_price, side_from_signed_qty, signed_qty, summary_key


def test_leg_reference_price_prefers_explicit_reference():
    leg = {"reference_price": 42.25, "notional_usd": 1000, "quantity_estimate": 10}
    assert leg_reference_price(leg) == 42.25


def test_leg_reference_price_falls_back_to_notional_per_share():
    leg = {"notional_usd": 1000, "quantity_estimate": 20}
    assert leg_reference_price(leg) == 50.0


def test_future_reference_price_uses_multiplier():
    leg = {"notional_usd": 1000, "quantity_estimate": 2, "contract_multiplier_coin": 0.1}
    assert leg_reference_price(leg, is_future=True) == 5000.0


def test_signed_qty_helpers():
    assert signed_qty("BUY", 10) == 10
    assert signed_qty("SELL", 10) == -10
    assert side_from_signed_qty(5) == "BUY"
    assert side_from_signed_qty(-5) == "SELL"


def test_summary_key_converts_ibkr_tags():
    assert summary_key("NetLiquidation") == "net_liquidation"
    assert summary_key("AvailableFunds") == "available_funds"


def test_dashboard_log_helpers():
    assert mask_account("DU1234567") == "DU***67"
    assert count_order_submissions([
        {"legs": [{"status": "Filled"}, {"diag": "already_at_target_or_open"}]},
        {"legs": [{"status": "Cancelled"}]},
    ]) == 1


def test_make_order_sets_tif_and_uses_fallback_quote(monkeypatch):
    executor = IBKRCarryExecutor()
    monkeypatch.setenv("IBKR_ACCOUNT", "DU1234567")
    monkeypatch.setenv("IBKR_ORDER_TIF", "DAY")
    monkeypatch.setattr(executor, "quote", lambda contract: {"bid": 0.0, "ask": 0.0, "last": 0.0, "close": 0.0, "market": 0.0})

    order, diag = executor.make_order(Stock("IBIT", "SMART", "USD"), "BUY", 10, is_future=False, fallback_ref=100.0)

    assert order.tif == "DAY"
    assert order.account == "DU1234567"
    assert order.lmtPrice == 100.05
    assert "fallback_ref=100.0" in diag
