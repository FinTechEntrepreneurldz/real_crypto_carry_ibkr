#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from real_crypto_carry_ibkr.artifacts import write_artifacts
from real_crypto_carry_ibkr.config import load_config
from real_crypto_carry_ibkr.data import build_provenance, load_curve, load_long_prices
from real_crypto_carry_ibkr.strategy import run_research


def main() -> None:
    p = argparse.ArgumentParser(description="Build a real-data-only crypto carry artifact.")
    p.add_argument("--curve-csv", required=True)
    p.add_argument("--long-prices-csv", required=True)
    p.add_argument("--out-dir", default="artifacts/latest")
    p.add_argument("--data-source", required=True, help="Must be an accepted real data source from config.")
    p.add_argument("--config", default=None)
    p.add_argument("--no-grid-search", action="store_true", help="Use static config parameters instead of validation-first grid selection.")
    args = p.parse_args()

    cfg = load_config(args.config)
    provenance = build_provenance(args.curve_csv, args.long_prices_csv, args.data_source, cfg)
    curve = load_curve(args.curve_csv)
    prices = load_long_prices(args.long_prices_csv)
    research = run_research(curve, prices, cfg, run_grid=False if args.no_grid_search else None)
    zip_path = write_artifacts(args.out_dir, cfg, provenance, research, args.curve_csv, args.long_prices_csv)
    status = json.loads((Path(args.out_dir) / "status.json").read_text(encoding="utf-8"))
    print(json.dumps({"artifact_zip": str(zip_path), **status}, indent=2, default=str))


if __name__ == "__main__":
    main()
