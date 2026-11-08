from __future__ import annotations

import numpy as np
import pandas as pd

from .features import FEATURE_ORDER


def validate_matrix(matrix: pd.DataFrame) -> None:
    expected = FEATURE_ORDER + ["symbol"]
    missing = [column for column in expected if column not in matrix.columns]
    if missing:
        raise ValueError(f"Eksik model girdileri: {missing}")
    if len(FEATURE_ORDER) != 132 or len(set(FEATURE_ORDER)) != 132:
        raise ValueError("feature_order.json tam olarak 132 benzersiz feature içermeli")
    if matrix["symbol"].isna().any():
        raise ValueError("symbol boş olamaz")


def prepare_lightgbm_input(artifact: dict, matrix: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Joblib artifact'ına tam uygun 132 float32 + categorical symbol üretir."""
    validate_matrix(matrix)
    expected = FEATURE_ORDER + ["symbol"]
    actual = list(artifact["model"].feature_name_)
    if actual != expected:
        raise ValueError(f"Model feature sırası uyumsuz. Beklenen={expected}, model={actual}")
    symbol_map = artifact["symbol_map"]
    supported = matrix["symbol"].astype(str).isin(symbol_map)
    selected = matrix.loc[supported].copy()
    x = selected[FEATURE_ORDER].astype("float32").replace([np.inf, -np.inf], np.nan)
    mapped = selected["symbol"].astype(str).map(symbol_map).astype("int64")
    categories = artifact["model"].booster_.pandas_categorical[0]
    x["symbol"] = pd.Categorical(mapped, categories=categories)
    if list(x.columns) != expected:
        raise AssertionError("Model input kolon sırası bozuldu")
    return selected, x
