import csv
import json
import subprocess
import sys
from pathlib import Path


def test_write_dashboard_snapshot_from_account_summary(tmp_path: Path):
    summary_path = tmp_path / "account_summary.json"
    log_dir = tmp_path / "logs"
    summary_path.write_text(
        json.dumps(
            {
                "account": "DU1234567",
                "net_liquidation": 1_234_567.89,
                "currency": "USD",
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/write_dashboard_snapshot.py",
            "--account-summary",
            str(summary_path),
            "--log-dir",
            str(log_dir),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["account"] == "DU***67"
    portfolio_rows = list(csv.DictReader((log_dir / "portfolio" / "portfolio.csv").open()))
    decision_rows = list(csv.DictReader((log_dir / "decisions" / "latest_decision.csv").open()))
    health = json.loads((log_dir / "health" / "health_status.json").read_text())

    assert portfolio_rows[-1]["portfolio_value"] == "1234567.89"
    assert portfolio_rows[-1]["submit_orders"] == "False"
    assert decision_rows[-1]["n_orders_submitted"] == "0"
    assert health["account_id_masked"] == "DU***67"
