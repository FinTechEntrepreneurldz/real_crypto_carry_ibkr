import json
import os
import subprocess
import sys
from pathlib import Path


def _write_artifact(path: Path, status: str = "RESEARCH_ONLY_NOT_DEPLOYABLE") -> None:
    path.mkdir(parents=True)
    (path / "status.json").write_text(
        json.dumps({"status": status, "reason": "test Sharpe below target"}),
        encoding="utf-8",
    )
    (path / "execution_plan_latest.json").write_text(
        json.dumps(
            [
                {
                    "asset": "BTC",
                    "long_leg": {
                        "side": "BUY",
                        "quantity_estimate": 1,
                        "symbol": "IBIT",
                    },
                }
            ]
        ),
        encoding="utf-8",
    )


def test_research_only_artifact_still_allows_dry_run(tmp_path: Path):
    artifact_dir = tmp_path / "artifact"
    _write_artifact(artifact_dir)

    result = subprocess.run(
        [sys.executable, "scripts/run_ibkr_rebalance.py", "--artifact-dir", str(artifact_dir), "--dry-run"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["deployable"] is False
    assert payload["artifact_status"] == "RESEARCH_ONLY_NOT_DEPLOYABLE"
    assert payload["results"][0]["legs"][0]["contract"] == "IBIT"


def test_research_only_artifact_blocks_submission_before_ibkr(tmp_path: Path):
    artifact_dir = tmp_path / "artifact"
    _write_artifact(artifact_dir)
    env = {**os.environ, "IBKR_SUBMIT_ORDERS": "true"}

    result = subprocess.run(
        [sys.executable, "scripts/run_ibkr_rebalance.py", "--artifact-dir", str(artifact_dir)],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    assert result.returncode != 0
    assert "Artifact is not deployable" in result.stderr
    assert "test Sharpe below target" in result.stderr
