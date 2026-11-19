#!/usr/bin/env python3
"""Per-symbol feature generation, model inference and candidate simulation."""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "feature_generator_portable"))
sys.path.insert(0, str(HERE.parent / "paper_trade_app"))

from data import (
    CACHE,
    CANDIDATES,
    PACKAGE,
    SLIPPAGE_4H,
    TF_MS,
    TIMEFRAMES,
    ensure_dirs,
)
from feature_engine.features import FEATURE_ORDER, compute_features
from model_registry import ModelRegistry, load_scenarios
from trade_sim import (
    CandidateBatch,
    add_pnl,
    concat_batches,
    empty_batch,
    run_exits,
)

# ---------------------------------------------------------------------------
# Worker state
# ---------------------------------------------------------------------------

REGISTRY: ModelRegistry | None = None
SPECS_BY_TF: dict[str, list] = {}
CONTEXT: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}
WARMUP_BARS = 200


EMPTY_DTYPES = {
    "scenario_id": "int16",
    "symbol": "string",
    "signal_time": "int64",
    "score": "float32",
    "range_value": "float32",
    "vol_value": "float32",
    "signal_close": "float64",
    "entry_time": "int64",
    "entry_price": "float64",
    "tp_price": "float64",
    "sl_price": "float64",
    "exit_time": "int64",
    "exit_price": "float64",
    "outcome_code": "int8",
    "timeframe": "string",
    "gross_pnl_tl": "float64",
    "fees_tl": "float64",
    "funding_tl": "float64",
    "net_pnl_tl": "float64",
    "net_return_pct": "float64",
    "hold_hours": "float64",
    "outcome": "string",
}


def empty_candidate_frame() -> pd.DataFrame:
    return pd.DataFrame({name: pd.Series(dtype=dtype)
                        for name, dtype in EMPTY_DTYPES.items()})
PREDICT_THREADS = max(1, int(os.environ.get("PREDICT_THREADS", "1")))


def init_worker() -> None:
    global REGISTRY, SPECS_BY_TF, CONTEXT
    REGISTRY = ModelRegistry(PACKAGE)
    specs = load_scenarios(PACKAGE, REGISTRY)
    SPECS_BY_TF = {tf: [s for s in specs if s.timeframe == tf and s.enabled]
                   for tf in TIMEFRAMES}
    CONTEXT = {}
    for tf in TIMEFRAMES:
        macro = pd.read_parquet(CACHE / f"macro_{tf}.parquet")
        market = pd.read_parquet(CACHE / f"market_{tf}.parquet")
        CONTEXT[tf] = (macro, market.set_index("open_time")["quote_volume"])


def regression_targets(specs: list) -> set[str]:
    targets: set[str] = set()
    for spec in specs:
        if spec.filter_name in {"range_full", "range_half_volatility",
                                "range_full_volatility"}:
            targets.add(f"future_range_{spec.horizon}mum_pct")
        if spec.filter_name in {"volatility", "range_half_volatility",
                                "range_full_volatility"}:
            targets.add(f"realized_volatility_{spec.horizon}mum_pct")
    return targets


def _fallback(target: str) -> tuple[str, str] | None:
    if target == "future_range_10mum_pct":
        return ("future_range_5mum_pct", "future_range_15mum_pct")
    if target == "realized_volatility_10mum_pct":
        return ("realized_volatility_5mum_pct",
                "realized_volatility_15mum_pct")
    return None


def predict_regression(registry: ModelRegistry, tf: str, target: str,
                       matrix: pd.DataFrame) -> np.ndarray:
    if registry.has_model(tf, target):
        artifact = registry.models[(tf, target)]
        return artifact["model"].booster_.predict(matrix, num_threads=PREDICT_THREADS)
    fallback = _fallback(target)
    if fallback is None:
        raise KeyError(f"Regression model missing: {tf} {target}")
    first = predict_regression(registry, tf, fallback[0], matrix)
    second = predict_regression(registry, tf, fallback[1], matrix)
    return (first + second) / 2.0


def classify(registry: ModelRegistry, tf: str, target: str,
             matrix: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    artifact = registry.models[(tf, target)]
    booster = artifact["model"].booster_
    probabilities = booster.predict(matrix, num_threads=PREDICT_THREADS)
    classes = list(artifact["classes"])
    tp_index = classes.index("TP")
    best = np.argmax(probabilities, axis=1)
    is_tp = best == tp_index
    return is_tp, probabilities[:, tp_index].astype("float32")


def build_batch(spec, idx: np.ndarray, feat: pd.DataFrame,
                signal_close: np.ndarray, one_min: pd.DataFrame,
                scores: np.ndarray, range_full: np.ndarray,
                vol_full: np.ndarray) -> CandidateBatch:
    if not len(idx):
        return empty_batch()
    open_time = feat["open_time"].to_numpy(dtype="int64")
    m_open_time = one_min["open_time"].to_numpy(dtype="int64")
    m_open = one_min["open"].to_numpy(dtype="float64")
    tf_ms = TF_MS[spec.timeframe]
    signal_time = open_time[idx]
    entry_time = signal_time + tf_ms
    entry_idx = np.searchsorted(m_open_time, entry_time, side="left")
    deadline = entry_time + spec.horizon * tf_ms
    last_ms = int(m_open_time[-1]) + 60_000
    valid = (entry_idx < len(m_open_time)) & (deadline <= last_ms)
    entry_price = np.where(
        spec.timeframe == "4h",
        signal_close[idx] * (1.0 + SLIPPAGE_4H),
        m_open[np.clip(entry_idx, 0, len(m_open) - 1)],
    )
    valid &= np.isfinite(entry_price) & (entry_price > 0)
    tp_price = np.where(
        spec.timeframe == "4h",
        signal_close[idx] * (1.0 + spec.tp_pct / 100.0),
        entry_price * (1.0 + spec.tp_pct / 100.0),
    )
    sl_price = np.where(
        spec.timeframe == "4h",
        signal_close[idx] * (1.0 - spec.sl_pct / 100.0),
        entry_price * (1.0 - spec.sl_pct / 100.0),
    )
    valid &= np.isfinite(tp_price) & np.isfinite(sl_price)
    if not valid.any():
        return empty_batch()
    idx = idx[valid]
    signal_time = signal_time[valid]
    entry_time = entry_time[valid]
    entry_idx = entry_idx[valid]
    entry_price = entry_price[valid]
    tp_price = tp_price[valid]
    sl_price = sl_price[valid]
    return CandidateBatch(
        np.full(len(idx), spec.scenario_id, dtype="int64"),
        signal_time.astype("int64"),
        scores[idx].astype("float32"),
        range_full[idx].astype("float32"),
        vol_full[idx].astype("float32"),
        signal_close[idx].astype("float64"),
        entry_time.astype("int64"),
        entry_price.astype("float64"),
        tp_price.astype("float64"),
        sl_price.astype("float64"),
        (entry_time + spec.horizon * tf_ms).astype("int64"),
        entry_idx.astype("int64"),
    )


def process_timeframe(symbol: str, tf: str, one_min: pd.DataFrame) -> pd.DataFrame:
    assert REGISTRY is not None
    specs = SPECS_BY_TF.get(tf, [])
    if not specs:
        return empty_candidate_frame()
    macro, market = CONTEXT[tf]
    from data import resample_bars
    bars = resample_bars(one_min, tf)
    if len(bars) <= WARMUP_BARS:
        return empty_candidate_frame()
    share = bars["quote_volume"] / bars["open_time"].map(market)
    feat = compute_features(bars, macro, share.reset_index(drop=True))
    signal_close = feat["close"].to_numpy(dtype="float64")
    feat = feat[["open_time", *FEATURE_ORDER]].copy()
    for column in FEATURE_ORDER:
        feat[column] = feat[column].astype("float32")
    n = len(feat)
    warm = np.zeros(n, dtype=bool)
    warm[WARMUP_BARS:] = True

    artifact = REGISTRY.models[(tf, specs[0].target)]
    categories = artifact["model"].booster_.pandas_categorical[0]
    symbol_map = artifact["symbol_map"]
    code = symbol_map.get(symbol, np.nan)
    matrix = feat[FEATURE_ORDER].copy()
    matrix["symbol"] = pd.Categorical([code] * n, categories=categories)

    classifier_targets = sorted({spec.target for spec in specs})
    scores: dict[str, np.ndarray] = {}
    is_tp: dict[str, np.ndarray] = {}
    for target in classifier_targets:
        started = time.time()
        tp, score = classify(REGISTRY, tf, target, matrix)
        is_tp[target] = tp
        scores[target] = score
        print(f"  {symbol} {tf} {target} {time.time() - started:.1f}s",
              flush=True)

    base: dict[int, np.ndarray] = {}
    for spec in specs:
        mask = is_tp[spec.target] & warm
        if spec.threshold is not None:
            mask &= scores[spec.target] >= spec.threshold
        base[spec.scenario_id] = mask

    reg_targets = regression_targets(specs)
    reg_rows: dict[str, np.ndarray] = {}
    for target in reg_targets:
        pieces = []
        for spec in specs:
            if spec.filter_name not in {
                "range_full", "range_half_volatility",
                "range_full_volatility", "volatility",
            }:
                continue
            needs = (
                target.startswith("future_range_") and
                spec.filter_name in {"range_full", "range_half_volatility",
                                     "range_full_volatility"}
            ) or (
                target.startswith("realized_volatility_") and
                spec.filter_name in {"volatility", "range_half_volatility",
                                     "range_full_volatility"}
            )
            if needs and f"{spec.horizon}mum_pct" in target:
                rows = np.flatnonzero(base[spec.scenario_id])
                if len(rows):
                    pieces.append(rows)
        reg_rows[target] = np.unique(np.concatenate(pieces)) if pieces else np.empty(0, dtype="int64")

    reg_full: dict[str, np.ndarray] = {}
    for target in reg_targets:
        values = np.full(n, np.nan, dtype="float32")
        rows = reg_rows[target]
        if len(rows):
            values[rows] = predict_regression(
                REGISTRY, tf, target, matrix.iloc[rows]
            ).astype("float32")
        reg_full[target] = values

    batches: list[CandidateBatch] = []
    for spec in specs:
        mask = base[spec.scenario_id].copy()
        range_target = f"future_range_{spec.horizon}mum_pct"
        vol_target = f"realized_volatility_{spec.horizon}mum_pct"
        range_values = reg_full.get(range_target, np.full(n, np.nan))
        vol_values = reg_full.get(vol_target, np.full(n, np.nan))
        vol_pass = (vol_values >= spec.vol_low) & (vol_values <= spec.vol_high)
        if spec.filter_name == "range_full":
            mask &= range_values >= spec.tp_pct
        elif spec.filter_name == "range_half_volatility":
            mask &= (range_values >= spec.tp_pct / 2.0) & vol_pass
        elif spec.filter_name == "range_full_volatility":
            mask &= (range_values >= spec.tp_pct) & vol_pass
        elif spec.filter_name == "volatility":
            mask &= vol_pass
        idx = np.flatnonzero(mask)
        batch = build_batch(
            spec, idx, feat, signal_close, one_min, scores[spec.target],
            range_values, vol_values,
        )
        if len(batch.scenario_id):
            batches.append(batch)

    combined = concat_batches(batches)
    if not len(combined.scenario_id):
        return empty_candidate_frame()
    frame = run_exits(combined, one_min, symbol, tf)
    return add_pnl(frame)


def process_symbol(symbol: str) -> dict:
    started = time.time()
    marker = CANDIDATES / f"{symbol}.done"
    outputs = [CANDIDATES / f"{symbol}__{tf}.parquet" for tf in TIMEFRAMES]
    if marker.exists() and all(path.exists() for path in outputs):
        return {"symbol": symbol, "status": "cached", "seconds": 0}
    try:
        from data import load_1m
        one_min = load_1m(symbol)
        if len(one_min) < 300:
            return {"symbol": symbol, "status": "short", "rows_1m": len(one_min)}
        stats = {}
        for tf in TIMEFRAMES:
            path = CANDIDATES / f"{symbol}__{tf}.parquet"
            if path.exists():
                stats[tf] = int(pd.read_parquet(path, columns=["scenario_id"]).shape[0])
                continue
            frame = process_timeframe(symbol, tf, one_min)
            frame.to_parquet(path, index=False, compression="zstd")
            stats[tf] = int(len(frame))
            print(f"{symbol} {tf} candidates={len(frame):,} "
                  f"sec={time.time() - started:.1f}", flush=True)
        marker.write_text(json.dumps(stats), encoding="utf-8")
        return {"symbol": symbol, "status": "ok", "seconds": round(time.time() - started, 1),
                "candidates": stats, "rows_1m": int(len(one_min))}
    except Exception as exc:
        traceback.print_exc()
        return {"symbol": symbol, "status": "error", "error": f"{type(exc).__name__}: {exc}"}


if __name__ == "__main__":
    ensure_dirs()
    init_worker()
    import sys as _sys
    print(process_symbol(_sys.argv[1]))
