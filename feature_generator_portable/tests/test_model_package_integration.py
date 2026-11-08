from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from feature_engine import FEATURE_ORDER, compute_latest_matrix, prepare_lightgbm_input


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT.parent / "PAPER_TRADE_POSITIVE_MODELS_1M_EXECUTION" / "paper_trade_package"


def candles(symbol_shift: float, count: int = 500) -> pd.DataFrame:
    step = np.arange(count, dtype="float64")
    open_time = step.astype("int64") * 300_000 + 1_700_000_000_000
    close = 100 + symbol_shift + step * 0.02 + np.sin(step / 9) * 0.8
    volume = 2_000 + step * 3 + 30 * np.cos(step / 11)
    return pd.DataFrame(
        {
            "open_time": open_time,
            "open": close - 0.08,
            "high": close + 0.55,
            "low": close - 0.48,
            "close": close,
            "volume": volume,
            "close_time": open_time + 299_999,
            "quote_volume": close * volume,
            "trade_count": 150 + step % 30,
            "taker_buy_base_volume": volume * 0.51,
            "taker_buy_quote_volume": close * volume * 0.51,
        }
    )


def test_all_packaged_models_accept_generated_features():
    package_order = json.loads((PACKAGE / "feature_order.json").read_text(encoding="utf-8"))
    assert FEATURE_ORDER == package_order

    manifest = pd.read_csv(PACKAGE / "model_manifest.csv")
    first = joblib.load(PACKAGE / manifest.iloc[0].relative_path)
    all_symbols = sorted(first["symbol_map"])
    assert len(all_symbols) == 106
    symbols = all_symbols
    frames = {symbol: candles(i * 0.07) for i, symbol in enumerate(symbols)}
    server_time = int(frames["BTCUSDT"]["close_time"].max()) + 1
    matrix, _ = compute_latest_matrix(frames, server_time)
    assert matrix.shape[0] == 106
    values = matrix[FEATURE_ORDER].to_numpy(dtype=float)
    assert not np.isinf(values).any()

    for row in manifest.itertuples(index=False):
        artifact = joblib.load(PACKAGE / row.relative_path)
        selected, model_input = prepare_lightgbm_input(artifact, matrix)
        assert not selected.empty
        model = artifact["model"]
        prediction = (
            model.predict_proba(model_input)
            if hasattr(model, "predict_proba")
            else model.predict(model_input)
        )
        assert len(prediction) == len(selected)
        assert np.isfinite(np.asarray(prediction, dtype=float)).all()
