from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "paper_trade_app"))

from candle_history import CandleHistoryStore
from config import Settings
from exact_features import latest_exact_matrix
from model_registry import ModelRegistry, load_scenarios


CASES = [
    {"scenario_id": 17, "symbol": "DOGEUSDT", "signal": "2025-12-09 16:00:00+00:00"},
    {"scenario_id": 49, "symbol": "BANDUSDT", "signal": "2025-12-15 16:00:00+00:00"},
    {"scenario_id": 111, "symbol": "ADAUSDT", "signal": "2025-12-18 00:00:00+00:00"},
]


def decision(spec, cls, range_value, vol_value):
    score = float(cls["tp_score"])
    class_pass = cls["prediction"] == "TP"
    threshold_pass = spec.threshold is None or score >= spec.threshold
    vol_pass = vol_value is not None and spec.vol_low <= vol_value <= spec.vol_high
    filters = {
        "signal_only": True,
        "threshold": True,
        "range_full": range_value is not None and range_value >= spec.tp_pct,
        "range_half_volatility": range_value is not None and range_value >= spec.tp_pct / 2 and vol_pass,
        "range_full_volatility": range_value is not None and range_value >= spec.tp_pct and vol_pass,
        "volatility": vol_pass,
    }
    filter_pass = filters.get(spec.filter_name, False)
    return class_pass and threshold_pass and filter_pass, class_pass, threshold_pass, filter_pass


settings = Settings()
registry = ModelRegistry(settings.package_dir)
specs = {item.scenario_id: item for item in load_scenarios(settings.package_dir, registry)}
store = CandleHistoryStore(settings.history_db_path)
all_frames: dict[str, dict[str, pd.DataFrame]] = {}
try:
    for timeframe in {specs[item["scenario_id"]].timeframe for item in CASES}:
        all_frames[timeframe] = {
            symbol: store.load(timeframe, symbol) for symbol in registry.symbols
        }
    results = []
    for case in CASES:
        started = time.perf_counter()
        spec = specs[case["scenario_id"]]
        signal = pd.Timestamp(case["signal"])
        frames = {}
        for symbol, frame in all_frames[spec.timeframe].items():
            sliced = frame.loc[:signal]
            if not sliced.empty:
                frames[symbol] = sliced
        matrix, missing = latest_exact_matrix(frames)
        classifier = registry.predict(spec.timeframe, spec.target, matrix)
        range_target = f"future_range_{spec.horizon}mum_pct"
        vol_target = f"realized_volatility_{spec.horizon}mum_pct"
        ranges = registry.predict(spec.timeframe, range_target, matrix)
        vols = registry.predict(spec.timeframe, vol_target, matrix)
        symbol = case["symbol"]
        target_missing_columns = matrix.loc[symbol, registry.feature_order].index[
            matrix.loc[symbol, registry.feature_order].isna()
        ].tolist()
        cls = classifier[symbol]
        range_value = ranges.get(symbol)
        vol_value = vols.get(symbol)
        accepted, class_pass, threshold_pass, filter_pass = decision(
            spec, cls, range_value, vol_value
        )
        results.append({
            **case,
            "timeframe": spec.timeframe,
            "scenario": spec.scenario,
            "model_prediction": cls["prediction"],
            "tp_score": float(cls["tp_score"]),
            "threshold": spec.threshold,
            "range_prediction": range_value,
            "volatility_prediction": vol_value,
            "volatility_bounds": [spec.vol_low, spec.vol_high],
            "class_pass": class_pass,
            "threshold_pass": threshold_pass,
            "filter_pass": filter_pass,
            "paper_would_accept": accepted,
            "feature_missing": missing,
            "target_symbol_missing_features": target_missing_columns,
            "universe_rows": len(matrix),
            "seconds": round(time.perf_counter() - started, 3),
        })
    print(json.dumps(results, ensure_ascii=False, indent=2))
finally:
    store.close()
