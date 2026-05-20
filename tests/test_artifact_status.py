from types import SimpleNamespace

import pandas as pd

from real_crypto_carry_ibkr.artifacts import evaluate_status, latest_execution_plan


def _cfg():
    return {
        "strategy": {
            "target_sharpe": 2.0,
            "target_ann_return": 0.15,
            "max_test_drawdown": -0.30,
            "min_test_days": 5,
        }
    }


def test_deployable_when_real_and_gates_pass():
    summary = pd.DataFrame([{"period": "test", "sharpe": 2.5, "ann_return": 0.20, "max_dd": -0.10, "n": 10}])
    prov = SimpleNamespace(accepted=True, reason="accepted")
    status = evaluate_status(summary, prov, [{"asset": "BTC"}], _cfg())
    assert status["status"] == "DEPLOYABLE_IBKR_CARRY"


def test_not_deployable_when_real_gates_fail():
    summary = pd.DataFrame([{"period": "test", "sharpe": 1.2, "ann_return": 0.20, "max_dd": -0.10, "n": 10}])
    prov = SimpleNamespace(accepted=True, reason="accepted")
    status = evaluate_status(summary, prov, [{"asset": "BTC"}], _cfg())
    assert status["status"] == "RESEARCH_ONLY_NOT_DEPLOYABLE"
    assert "Sharpe" in status["reason"]


def test_latest_execution_plan_uses_capital_and_gross_cap():
    positions = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2026-05-20"),
                "asset": "BTC",
                "target_weight": 3.0,
                "long_close": 50.0,
                "future_settle": pd.NA,
                "basis_ann": 0.0,
                "long_symbol": "IBIT",
            },
            {
                "date": pd.Timestamp("2026-05-20"),
                "asset": "ETH",
                "target_weight": -1.0,
                "long_close": 20.0,
                "future_settle": pd.NA,
                "basis_ann": 0.0,
                "long_symbol": "ETHA",
            },
        ]
    )
    cfg = {
        "strategy": {
            "capital_usd": 1_000_000,
            "execution_gross_cap": 1.0,
            "hedge_ratio": 0.0,
        },
        "assets": {"BTC": {"long_symbol": "IBIT"}, "ETH": {"long_symbol": "ETHA"}},
    }

    plan = latest_execution_plan(positions, cfg)

    assert [row["long_leg"]["side"] for row in plan] == ["SELL", "BUY"]
    assert sum(row["long_leg"]["notional_usd"] for row in plan) == 1_000_000
    btc = next(row for row in plan if row["asset"] == "BTC")
    assert btc["target_weight"] == 0.75
    assert btc["model_target_weight"] == 3.0
    assert btc["execution_scale"] == 0.25
