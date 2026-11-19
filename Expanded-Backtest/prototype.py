#!/usr/bin/env python3
"""Prototype: reproduce scenario 1 signals for SNXUSDT on derived 5m bars."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "feature_generator_portable"))
sys.path.insert(0, str(HERE.parent / "paper_trade_app"))

from data import FEATURE_ROOT, PACKAGE, WORKBOOK, load_1m, resample_bars
from feature_engine.features import FEATURE_ORDER, build_macro, compute_features
from model_registry import ModelRegistry, load_scenarios


def predict_batch(artifact: dict, frame: pd.DataFrame, symbol: str) -> tuple[np.ndarray, np.ndarray]:
    matrix = frame[FEATURE_ORDER].astype("float32").replace([np.inf, -np.inf], np.nan)
    if symbol in artifact["symbol_map"]:
        code = artifact["symbol_map"][symbol]
    else:
        code = np.nan
    categories = artifact["model"].booster_.pandas_categorical[0]
    matrix["symbol"] = pd.Categorical([code] * len(matrix), categories=categories)
    probabilities = artifact["model"].predict_proba(matrix)
    classes = list(artifact["classes"])
    tp_idx = classes.index("TP")
    best = np.argmax(probabilities, axis=1)
    return np.array([classes[int(i)] for i in best]), probabilities[:, tp_idx]


def main() -> None:
    started = time.time()
    print("loading BTC/ETH", flush=True)
    btc = resample_bars(load_1m("BTCUSDT"), "5m")
    eth = resample_bars(load_1m("ETHUSDT"), "5m")
    macro = build_macro({"BTCUSDT": btc, "ETHUSDT": eth})
    print("loading SNX", flush=True)
    bars = resample_bars(load_1m("SNXUSDT"), "5m")
    print("bars", len(bars), "features", flush=True)
    features = compute_features(bars, macro, None)
    print("feature seconds", round(time.time() - started, 1), flush=True)

    registry = ModelRegistry(PACKAGE)
    specs = {s.scenario_id: s for s in load_scenarios(PACKAGE, registry)}
    spec = specs[1]
    artifact = registry.models[("5m", spec.target)]
    print("predicting", spec.target, flush=True)
    predictions, scores = predict_batch(artifact, features, "SNXUSDT")
    print("predict seconds", round(time.time() - started, 1), flush=True)
    mask = (predictions == "TP") & (scores >= spec.threshold)
    signals = features.loc[mask, ["open_time", "close"]].copy()
    signals["score"] = scores[mask]
    print("raw threshold signals", len(signals))

    trades = pd.read_excel(WORKBOOK, sheet_name="Detayli_Islemler")
    trades = trades[(trades.scenario_id == 1) & (trades.symbol == "SNXUSDT")].copy()
    # workbook times are UTC+3; convert to UTC ms
    trades["signal_ms"] = (
        pd.to_datetime(trades.signal_time).dt.tz_localize("Europe/Istanbul")
        .dt.tz_convert("UTC").astype("datetime64[ms, UTC]").astype("int64")
    )
    base = signals[["open_time", "close", "score"]].rename(columns={"open_time": "signal_ms"})
    merged = base.merge(trades[["signal_ms", "tp_score", "outcome"]], on="signal_ms", how="outer", indicator=True)
    print(merged["_merge"].value_counts().to_dict())
    print("workbook", len(trades), "our raw", len(signals))
    print("workbook signal range", trades.signal_ms.min(), trades.signal_ms.max())
    print("our signal range", signals.open_time.min(), signals.open_time.max())
    both = merged[merged._merge == "both"]
    if len(both):
        print("score diff", (both.score - both.tp_score).abs().describe().to_dict())
    print(merged.sort_values("signal_ms").head(20).to_string())


if __name__ == "__main__":
    main()
