#!/usr/bin/env python3
"""Validate the Singular-Backtest outputs for paper-ml-bsh.

Checks:
  1. No two accepted trades of the same scope key overlap.
  2. Every accepted singular trade exists in the normal candidate set.
  3. No duplicate rows were introduced.

Run after `run_singular.py`:
    python Singular-Backtest/verify_singular.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
SCOPE_KEYS = {
    "scenario": ["scenario_id", "symbol"],
    "scenario_name": ["scenario", "symbol"],
    "strategy": ["strategy", "symbol"],
}


def overlap_count(df: pd.DataFrame, keys: list[str]) -> int:
    bad = 0
    for _, g in df.groupby(keys, sort=False, observed=True):
        g = g.sort_values(["signal_time", "entry_time"], kind="mergesort")
        if len(g) < 2:
            continue
        bad += int((g["signal_time"] < g["exit_time"].shift(1)).sum())
    return bad


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=ROOT / "results")
    ap.add_argument("--scope", choices=list(SCOPE_KEYS))
    args = ap.parse_args()

    base = args.results
    scope = args.scope
    if scope is None:
        manifest = base / "run_manifest.json"
        scope = (json.loads(manifest.read_text(encoding="utf-8"))["scope"]
                 if manifest.exists() else "scenario")

    cols = ["scenario_id", "scenario", "strategy", "symbol", "timeframe",
            "signal_time", "entry_time", "exit_time", "net_pnl_tl"]
    normal = pd.read_parquet(base / "normal_trades.parquet", columns=cols)
    singular = pd.read_parquet(base / "singular_trades.parquet", columns=cols)
    for df in (normal, singular):
        for col in ("signal_time", "entry_time", "exit_time"):
            df[col] = pd.to_datetime(df[col])

    print(f"scope           : {scope}")
    print(f"normal trades   : {len(normal):,}")
    print(f"singular trades : {len(singular):,}")
    print(f"blocked         : {len(normal) - len(singular):,}")

    keys = SCOPE_KEYS[scope]
    violations = overlap_count(singular, keys)
    print(f"singular overlaps ({'+'.join(keys)}): {violations}")

    subset_keys = ["scenario_id", "symbol", "signal_time", "entry_time"]
    merged = singular[subset_keys].merge(
        normal[subset_keys].drop_duplicates(), on=subset_keys,
        how="left", indicator=True,
    )
    missing = int((merged["_merge"] != "both").sum())
    print(f"singular trades missing in normal: {missing}")
    dupes = int(singular.duplicated(subset_keys).sum())
    print(f"duplicate singular rows          : {dupes}")

    blocked = len(normal) - len(singular)
    pct = blocked / len(normal) * 100 if len(normal) else 0.0
    print(f"blocked ratio   : {pct:.2f}%")
    if violations or missing or dupes:
        print("FAILED")
        raise SystemExit(1)
    print("OK")


if __name__ == "__main__":
    main()
