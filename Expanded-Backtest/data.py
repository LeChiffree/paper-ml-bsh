#!/usr/bin/env python3
"""Shared paths, constants and 1m data helpers for the expanded backtest."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
PACKAGE = REPO / "PAPER_TRADE_POSITIVE_MODELS_1M_EXECUTION" / "paper_trade_package"
WORKBOOK = PACKAGE / "BACKTEST_POSITIVE_METHODS_1M_EXECUTION.xlsx"
FEATURE_ROOT = REPO / "feature_generator_portable"
MODEL_DIR = PACKAGE / "models"
DATA_ROOT = Path(r"D:\1mdatabinance\1m")
CACHE = ROOT / "cache"
CANDIDATES = ROOT / "candidates"
RESULTS = ROOT / "results"

TIMEFRAMES = ("5m", "15m", "1h", "4h")
TF_MINUTES = {"5m": 5, "15m": 15, "1h": 60, "4h": 240}
TF_MS = {name: minutes * 60_000 for name, minutes in TF_MINUTES.items()}
STAKE_TL = 10_000.0
INITIAL_TL = 100_000.0
MAX_OPEN = 10
FEE_RATE = {"5m": 0.001, "15m": 0.001, "1h": 0.001, "4h": 0.0005}
SLIPPAGE_4H = 0.0002
FUNDING_PER_8H = 0.000108
DATA_END_MS = 1_794_873_600_000 + 3 * 3_600_000  # 2026-11-18 00:00 TRT

NUMERIC_1M = [
    "open", "high", "low", "close", "volume", "quote_asset_volume",
    "number_of_trades", "taker_buy_base_asset_volume",
    "taker_buy_quote_asset_volume",
]
RENAME_1M = {
    "quote_asset_volume": "quote_volume",
    "number_of_trades": "trade_count",
    "taker_buy_base_asset_volume": "taker_buy_base_volume",
    "taker_buy_quote_asset_volume": "taker_buy_quote_volume",
}
BAR_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "quote_volume",
    "trade_count", "taker_buy_base_volume", "taker_buy_quote_volume",
    "close_time",
]


def ensure_dirs() -> None:
    for path in (CACHE, CANDIDATES, RESULTS):
        path.mkdir(parents=True, exist_ok=True)


def parquet_files(symbol: str) -> list[Path]:
    return sorted((DATA_ROOT / symbol).glob("*.parquet"))


def csv_files(symbol: str) -> list[Path]:
    return sorted((DATA_ROOT / symbol).glob("*.csv"))


def list_symbols() -> list[str]:
    return sorted(p.name for p in DATA_ROOT.iterdir() if p.is_dir())


def load_1m(symbol: str, columns: list[str] | None = None) -> pd.DataFrame:
    """Load every monthly/daily file for a symbol and normalise the schema."""
    folder = DATA_ROOT / symbol
    frames: list[pd.DataFrame] = []
    wanted = columns or BAR_COLUMNS
    for path in sorted(folder.iterdir()):
        try:
            if path.suffix == ".parquet":
                frame = pd.read_parquet(path)
            elif path.suffix == ".csv":
                frame = pd.read_csv(path, encoding="utf-8-sig",
                                    on_bad_lines="skip")
            else:
                continue
        except Exception:
            continue
        if frame.empty:
            continue
        frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=wanted)
    df = pd.concat(frames, ignore_index=True)
    if "open_time" not in df.columns:
        raise ValueError(f"{symbol}: open_time missing")
    df = df.rename(columns=RENAME_1M)
    for name in NUMERIC_1M:
        source = name
        if name == "quote_asset_volume":
            source = "quote_volume"
        elif name == "number_of_trades":
            source = "trade_count"
        elif name == "taker_buy_base_asset_volume":
            source = "taker_buy_base_volume"
        elif name == "taker_buy_quote_asset_volume":
            source = "taker_buy_quote_volume"
        if source in df.columns:
            df[source] = pd.to_numeric(df[source], errors="coerce")
    if "close_time" not in df.columns:
        df["close_time"] = df["open_time"] + 59_999
    df["open_time"] = pd.to_numeric(df["open_time"], errors="coerce").astype("int64")
    df["close_time"] = pd.to_numeric(df["close_time"], errors="coerce").astype("int64")
    df = df.dropna(subset=["open_time", "open", "high", "low", "close"])
    df = df[df["open_time"] > 0]
    df = df[df["high"] > 0]
    df = df[df["low"] > 0]
    df = df[df["open"] > 0]
    df = df[df["close"] > 0]
    df = df[df["high"] >= df["low"]]
    df = df.drop_duplicates("open_time", keep="last")
    df = df.sort_values("open_time", kind="mergesort").reset_index(drop=True)
    for name in BAR_COLUMNS:
        if name not in df.columns:
            df[name] = np.nan
    return df[BAR_COLUMNS]


def resample_bars(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Aggregate 1m OHLCV rows into the requested timeframe."""
    ms = TF_MS[timeframe]
    bucket = (df["open_time"].to_numpy() // ms) * ms
    grouped = df.groupby(bucket, sort=True)
    bars = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        quote_volume=("quote_volume", "sum"),
        trade_count=("trade_count", "sum"),
        taker_buy_base_volume=("taker_buy_base_volume", "sum"),
        taker_buy_quote_volume=("taker_buy_quote_volume", "sum"),
        close_time=("close_time", "last"),
    )
    bars.insert(0, "open_time", bars.index.astype("int64"))
    bars = bars.reset_index(drop=True)
    bars["close_time"] = bars["open_time"] + ms - 1
    return bars[BAR_COLUMNS]


def bars_from_quote(df: pd.DataFrame, timeframe: str) -> pd.Series:
    """Sum 1m quote volume into timeframe buckets (for market share)."""
    ms = TF_MS[timeframe]
    bucket = (df["open_time"].to_numpy() // ms) * ms
    values = df["quote_volume"].to_numpy(dtype="float64")
    out = pd.Series(values, index=bucket).groupby(level=0).sum()
    out.index = out.index.astype("int64")
    return out


def add_utc3(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, unit="ms", utc=True).dt.tz_convert(
        "Europe/Istanbul"
    ).dt.tz_localize(None)


def ms_from_utc3(values: pd.Series) -> pd.Series:
    return (
        pd.to_datetime(values)
        .dt.tz_localize("Europe/Istanbul")
        .dt.tz_convert("UTC")
        .astype("datetime64[ms, UTC]")
        .astype("int64")
    )
