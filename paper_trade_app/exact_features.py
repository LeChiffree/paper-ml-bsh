from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd


GENERATOR_ROOT = Path(__file__).resolve().parent.parent / "feature_generator_portable"
if not (GENERATOR_ROOT / "feature_engine" / "features.py").exists():
    raise RuntimeError(f"Exact feature generator is missing: {GENERATOR_ROOT}")
if str(GENERATOR_ROOT) not in sys.path:
    sys.path.insert(0, str(GENERATOR_ROOT))

from feature_engine.features import FEATURE_ORDER, compute_latest_matrix  # noqa: E402


def _to_portable_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Convert the service's UTC DatetimeIndex frame to the portable raw contract."""
    result = frame.reset_index(drop=True).copy()
    # ``DatetimeIndex.asi8`` follows the index's underlying resolution. Pandas
    # 3 may keep Binance timestamps as datetime64[ms], while older releases
    # commonly used datetime64[ns]. Convert explicitly to Unix milliseconds so
    # training, backtest and live inference share one cross-platform contract.
    timestamps = pd.DatetimeIndex(frame.index)
    open_time_ms = np.fromiter(
        (int(timestamp.timestamp() * 1000) for timestamp in timestamps),
        dtype="int64",
        count=len(timestamps),
    )
    result.insert(0, "open_time", open_time_ms)
    if pd.api.types.is_datetime64_any_dtype(result["close_time"]):
        result["close_time"] = (
            pd.DatetimeIndex(result["close_time"]).asi8 // 1_000_000
        ).astype("int64")
    else:
        result["close_time"] = pd.to_numeric(result["close_time"], errors="coerce").astype("int64")
    return result.drop_duplicates(subset="open_time", keep="last").sort_values("open_time").reset_index(drop=True)


def latest_exact_matrix(frames: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, int]:
    if "BTCUSDT" not in frames or "ETHUSDT" not in frames:
        raise ValueError("BTCUSDT and ETHUSDT are required for the exact feature contract")
    common_last = min(frame.index[-1] for frame in frames.values())
    portable = {
        symbol: _to_portable_frame(frame.loc[:common_last])
        for symbol, frame in frames.items()
        if len(frame.loc[:common_last]) >= 260
    }
    matrix, _ = compute_latest_matrix(
        portable, int(time.time() * 1000) + 1_000, retain_enriched=False
    )
    matrix = matrix.reindex(columns=FEATURE_ORDER + ["symbol"])
    values = matrix[FEATURE_ORDER].to_numpy(dtype=float)
    if np.isinf(values).any():
        raise ValueError("Exact feature generator returned infinity")
    return matrix, int(np.isnan(values).sum())
