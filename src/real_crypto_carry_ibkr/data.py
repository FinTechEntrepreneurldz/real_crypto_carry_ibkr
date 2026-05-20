from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd


CURVE_REQUIRED = {"date", "asset", "contract", "expiry", "settle"}
PRICES_REQUIRED = {"date", "asset", "symbol", "close"}
BLOCKED_TERMS = {"synthetic", "proxy", "sample", "demo", "yfinance", "yahoo"}


@dataclass(frozen=True)
class DataProvenance:
    source: str
    curve_path: str
    prices_path: str
    accepted: bool
    reason: str


def normalize_source(source: str) -> str:
    return str(source or "").strip().lower().replace(" ", "_")


def source_is_real(source: str, accepted_sources: Iterable[str], blocked_terms: Iterable[str] | None = None) -> tuple[bool, str]:
    src = normalize_source(source)
    blocked = {normalize_source(x) for x in (blocked_terms or BLOCKED_TERMS)}
    accepted = {normalize_source(x) for x in accepted_sources}
    if not src:
        return False, "missing data source"
    if any(term in src for term in blocked):
        return False, f"blocked data source term in {source!r}"
    if src not in accepted:
        return False, f"data source {source!r} is not in accepted_sources"
    return True, "accepted real data source"


def _load_csv(path: str | Path, required: set[str], name: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Missing {name}: {p}")
    df = pd.read_csv(p)
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{name} is missing required columns: {sorted(missing)}")
    return df


def load_curve(path: str | Path) -> pd.DataFrame:
    df = _load_csv(path, CURVE_REQUIRED, "curve CSV")
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
    df["expiry"] = pd.to_datetime(df["expiry"], utc=True).dt.tz_localize(None)
    df["asset"] = df["asset"].astype(str).str.upper().str.strip()
    df["contract"] = df["contract"].astype(str).str.upper().str.strip()
    df["settle"] = pd.to_numeric(df["settle"], errors="coerce")
    df = df.dropna(subset=["date", "expiry", "asset", "contract", "settle"])
    df = df[df["settle"] > 0].sort_values(["asset", "date", "expiry", "contract"])
    if df.empty:
        raise ValueError("curve CSV has no valid rows after cleaning")
    return df


def load_long_prices(path: str | Path) -> pd.DataFrame:
    df = _load_csv(path, PRICES_REQUIRED, "long prices CSV")
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
    df["asset"] = df["asset"].astype(str).str.upper().str.strip()
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    if "price_role" not in df.columns:
        df["price_role"] = "long"
    df["price_role"] = df["price_role"].astype(str).str.lower().str.strip()
    df = df.dropna(subset=["date", "asset", "symbol", "close"])
    df = df[df["close"] > 0].sort_values(["asset", "date", "symbol"])
    if df.empty:
        raise ValueError("long prices CSV has no valid rows after cleaning")
    return df


def build_provenance(curve_path: str | Path, prices_path: str | Path, source: str, cfg: dict) -> DataProvenance:
    ok, reason = source_is_real(
        source,
        cfg["data"]["accepted_sources"],
        cfg["data"].get("blocked_source_terms", BLOCKED_TERMS),
    )
    return DataProvenance(
        source=normalize_source(source),
        curve_path=str(Path(curve_path)),
        prices_path=str(Path(prices_path)),
        accepted=bool(ok),
        reason=reason,
    )
