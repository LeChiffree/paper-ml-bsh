#!/usr/bin/env python3
"""Build the cross-sectional market-volume series and BTC/ETH macro context."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import duckdb
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "feature_generator_portable"))

from data import CACHE, DATA_ROOT, TF_MS, TIMEFRAMES, ensure_dirs, load_1m, resample_bars
from feature_engine.features import build_macro


def build_market_5m() -> pd.DataFrame:
    pattern_parquet = str(DATA_ROOT / "*" / "*.parquet").replace("\\", "/")
    pattern_csv = str(DATA_ROOT / "*" / "*.csv").replace("\\", "/")
    query = f"""
        WITH f AS (
            SELECT * FROM read_parquet('{pattern_parquet}', union_by_name=true)
            UNION ALL
            SELECT * FROM read_csv('{pattern_csv}', union_by_name=true,
                                   header=true, ignore_errors=true)
        )
        SELECT (open_time // 300000) * 300000 AS open_time,
               sum(try_cast(quote_asset_volume AS DOUBLE)) AS quote_volume
        FROM f
        GROUP BY 1
        ORDER BY 1
    """
    return duckdb.sql(query).df()


def main() -> None:
    ensure_dirs()
    started = time.time()
    market_path = CACHE / "market_5m.parquet"
    if market_path.exists():
        market5 = pd.read_parquet(market_path)
        print(f"market cache {len(market5):,}", flush=True)
    else:
        print("scanning all 1m quote volume", flush=True)
        market5 = build_market_5m()
        market5.to_parquet(market_path, index=False, compression="zstd")
        print(f"market 5m rows={len(market5):,} sec={time.time() - started:.1f}", flush=True)

    for timeframe in TIMEFRAMES:
        target = CACHE / f"market_{timeframe}.parquet"
        if target.exists():
            continue
        if timeframe == "5m":
            series = market5.copy()
        else:
            ms = TF_MS[timeframe]
            bucket = (market5["open_time"].to_numpy() // ms) * ms
            values = market5["quote_volume"].to_numpy(dtype="float64")
            grouped = pd.Series(values, index=bucket).groupby(level=0).sum()
            series = pd.DataFrame({
                "open_time": grouped.index.astype("int64"),
                "quote_volume": grouped.to_numpy(),
            })
        series.to_parquet(target, index=False, compression="zstd")
        print(f"market {timeframe} rows={len(series):,}", flush=True)

    frames: dict[str, dict[str, pd.DataFrame]] = {}
    for symbol in ("BTCUSDT", "ETHUSDT"):
        raw = load_1m(symbol)
        frames[symbol] = {tf: resample_bars(raw, tf) for tf in TIMEFRAMES}
        print(f"{symbol} loaded {len(raw):,}", flush=True)

    for timeframe in TIMEFRAMES:
        macro = build_macro({
            "BTCUSDT": frames["BTCUSDT"][timeframe],
            "ETHUSDT": frames["ETHUSDT"][timeframe],
        })
        macro.to_parquet(CACHE / f"macro_{timeframe}.parquet", index=False,
                         compression="zstd")
        print(f"macro {timeframe} rows={len(macro):,}", flush=True)

    print(f"done sec={time.time() - started:.1f}", flush=True)


if __name__ == "__main__":
    main()
