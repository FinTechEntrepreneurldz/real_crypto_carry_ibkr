from pathlib import Path


def test_workflow_defaults_to_limit_orders_and_runtime_notional_guard():
    workflow = Path(".github/workflows/ibkr-paper-rebalance.yml").read_text(encoding="utf-8")

    assert 'IBKR_ALLOW_MARKET_ORDERS: "false"' in workflow
    assert "IBKR_LONG_LEG_ORDER_TYPE: ${{ vars.IBKR_LONG_LEG_ORDER_TYPE || 'LIMIT' }}" in workflow
    assert "IBKR_MAX_LONG_LEG_NOTIONAL_USD" in workflow
    assert "IBKR_LONG_LEG_AVAILABLE_FUNDS_FRACTION" in workflow
    assert "IBKR_CAPITAL_BASIS: ${{ vars.IBKR_CAPITAL_BASIS || 'net_liquidation' }}" in workflow


def test_workflow_dry_runs_but_only_submits_deployable_artifacts():
    workflow = Path(".github/workflows/ibkr-paper-rebalance.yml").read_text(encoding="utf-8")

    assert "steps.artifact_status.outputs.deployable == 'true' && env.IBKR_SUBMIT_ORDERS == 'true'" in workflow
    assert "No paper order submitted" in workflow
    assert "Write dashboard account snapshot without submitted orders" in workflow
