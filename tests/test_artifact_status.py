from types import SimpleNamespace

import pandas as pd

from real_crypto_carry_ibkr.artifacts import evaluate_status


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
