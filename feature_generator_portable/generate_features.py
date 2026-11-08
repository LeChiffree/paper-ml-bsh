from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from feature_engine import FEATURE_ORDER, compute_latest_matrix, prepare_lightgbm_input, validate_matrix

BINANCE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trade_count", "taker_buy_base_volume",
    "taker_buy_quote_volume", "ignore",
]
REQUIRED_RAW = BINANCE_COLUMNS[:-1]


def load_candles(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".parquet", ".pq"}:
        frame = pd.read_parquet(path)
    elif path.suffix.lower() == ".csv":
        frame = pd.read_csv(path)
    else:
        raise ValueError(f"Desteklenmeyen dosya: {path}")
    # Başlıksız Binance kline dışa aktarımlarını da kabul et.
    if not set(REQUIRED_RAW).issubset(frame.columns) and len(frame.columns) >= 11:
        if all(str(column).isdigit() for column in frame.columns):
            frame = frame.iloc[:, :min(12, len(frame.columns))]
            frame.columns = BINANCE_COLUMNS[:len(frame.columns)]
    missing = [column for column in REQUIRED_RAW if column not in frame.columns]
    if missing:
        raise ValueError(f"{path.name}: eksik ham mum kolonları {missing}")
    frame = frame.copy()
    frame["open_time"] = pd.to_numeric(frame["open_time"], errors="raise").astype("int64")
    frame["close_time"] = pd.to_numeric(frame["close_time"], errors="raise").astype("int64")
    for column in REQUIRED_RAW[1:]:
        if column != "close_time":
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "ignore" not in frame:
        frame["ignore"] = 0.0
    return frame.sort_values("open_time").drop_duplicates("open_time", keep="last").reset_index(drop=True)


def load_universe(input_dir: Path) -> dict[str, pd.DataFrame]:
    files = sorted([*input_dir.glob("*.parquet"), *input_dir.glob("*.pq"), *input_dir.glob("*.csv")])
    if not files:
        raise FileNotFoundError(f"{input_dir} içinde CSV/Parquet bulunamadı")
    frames = {}
    for path in files:
        symbol = path.stem.upper().replace("_KLINES", "").replace("_CANDLES", "")
        frames[symbol] = load_candles(path)
    return frames


def write_frame(frame: pd.DataFrame, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() == ".csv":
        frame.to_csv(output, index=False, encoding="utf-8-sig")
    elif output.suffix.lower() in {".parquet", ".pq"}:
        frame.to_parquet(output, index=False)
    else:
        raise ValueError("Çıktı uzantısı .csv veya .parquet olmalı")


def main() -> None:
    parser = argparse.ArgumentParser(description="132 feature + symbol model girdisi üretir")
    parser.add_argument("--input-dir", type=Path, required=True, help="SYMBOL.csv/parquet mum klasörü")
    parser.add_argument("--output", type=Path, required=True, help="latest_features.parquet veya .csv")
    parser.add_argument("--server-time-ms", type=int, default=None, help="Bu zamandan önce kapanan mumları kullan")
    parser.add_argument("--model", type=Path, help="Opsiyonel LightGBM .joblib; modele hazır dtype çıktısı doğrulanır")
    args = parser.parse_args()

    frames = load_universe(args.input_dir)
    server_time_ms = args.server_time_ms or int(time.time() * 1000)
    matrix, _ = compute_latest_matrix(frames, server_time_ms)
    output = matrix[FEATURE_ORDER + ["symbol"]].copy()
    output[FEATURE_ORDER] = output[FEATURE_ORDER].astype("float32").replace([np.inf, -np.inf], np.nan)
    validate_matrix(output)
    write_frame(output.reset_index(drop=True), args.output)

    report = {
        "symbols_loaded": len(frames), "rows_produced": len(output),
        "numeric_features": len(FEATURE_ORDER), "total_model_columns": 133,
        "server_time_ms": server_time_ms, "output": str(args.output),
    }
    if args.model:
        artifact = joblib.load(args.model)
        selected, model_x = prepare_lightgbm_input(artifact, output)
        report.update(model_supported_symbols=len(selected), model_input_dtypes={str(k): str(v) for k, v in model_x.dtypes.items()})
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
