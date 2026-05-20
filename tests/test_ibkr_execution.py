from real_crypto_carry_ibkr.ibkr_execution import leg_reference_price


def test_leg_reference_price_prefers_explicit_reference():
    leg = {"reference_price": 42.25, "notional_usd": 1000, "quantity_estimate": 10}
    assert leg_reference_price(leg) == 42.25


def test_leg_reference_price_falls_back_to_notional_per_share():
    leg = {"notional_usd": 1000, "quantity_estimate": 20}
    assert leg_reference_price(leg) == 50.0


def test_future_reference_price_uses_multiplier():
    leg = {"notional_usd": 1000, "quantity_estimate": 2, "contract_multiplier_coin": 0.1}
    assert leg_reference_price(leg, is_future=True) == 5000.0
