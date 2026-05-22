from ib_insync import Stock

from real_crypto_carry_ibkr.dashboard_logs import count_order_submissions, mask_account
from real_crypto_carry_ibkr.ibkr_execution import (
    IBKRCarryExecutor,
    default_long_leg_notional_cap,
    leg_reference_price,
    resolve_managed_account,
    side_from_signed_qty,
    signed_qty,
    summary_key,
)


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
    assert summary_key("BuyingPower") == "buying_power"


def test_resolve_managed_account_keeps_requested_match():
    resolved, auto_selected = resolve_managed_account("DU1234574", ["DU1234574"], auto_select=True)
    assert resolved == "DU1234574"
    assert auto_selected is False


def test_resolve_managed_account_auto_selects_single_paper_account():
    resolved, auto_selected = resolve_managed_account("DU1234574", ["DUQ335143"], auto_select=True)
    assert resolved == "DUQ335143"
    assert auto_selected is True


def test_resolve_managed_account_rejects_ambiguous_accounts():
    try:
        resolve_managed_account("DU1234574", ["DUQ335143", "DUQ304772"], auto_select=True)
    except RuntimeError as exc:
        assert "not in managed accounts" in str(exc)
    else:
        raise AssertionError("expected account mismatch to raise")


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


def test_default_long_leg_notional_cap_is_conservative_when_pair_cap_is_large(monkeypatch):
    monkeypatch.setenv("IBKR_MAX_PAIR_NOTIONAL_USD", "2000000")
    monkeypatch.delenv("IBKR_MAX_LONG_LEG_NOTIONAL_USD", raising=False)

    assert default_long_leg_notional_cap() == 250_000


def test_cap_long_leg_delta_caps_opening_stock_order_by_margin_budget(monkeypatch):
    executor = IBKRCarryExecutor()
    monkeypatch.setenv("IBKR_MAX_LONG_LEG_NOTIONAL_USD", "250000")
    monkeypatch.setenv("IBKR_LONG_LEG_AVAILABLE_FUNDS_FRACTION", "0.20")
    monkeypatch.setattr(executor, "account_summary", lambda: {"available_funds": 500_000})
    contract = Stock("IBIT", "SMART", "USD")
    contract.secType = "STK"

    capped, diag = executor.cap_long_leg_delta(
        contract,
        {"secType": "STK", "reference_price": 50.0},
        {"current_qty": 0, "delta_qty": 52_400, "side": "BUY", "quantity": 52_400},
    )

    assert capped["quantity"] == 2_000
    assert capped["delta_qty"] == 2_000
    assert "capped" in diag


def test_cap_long_leg_delta_does_not_cap_reducing_stock_order(monkeypatch):
    executor = IBKRCarryExecutor()
    monkeypatch.setenv("IBKR_MAX_LONG_LEG_NOTIONAL_USD", "1000")
    monkeypatch.setattr(executor, "account_summary", lambda: {"available_funds": 1_000})
    contract = Stock("IBIT", "SMART", "USD")
    contract.secType = "STK"

    capped, diag = executor.cap_long_leg_delta(
        contract,
        {"secType": "STK", "reference_price": 50.0},
        {"current_qty": 5_000, "delta_qty": -4_000, "side": "SELL", "quantity": 4_000},
    )

    assert capped["quantity"] == 4_000
    assert diag == "not_capped_reducing"
