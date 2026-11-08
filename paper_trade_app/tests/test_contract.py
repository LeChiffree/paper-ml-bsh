from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd


APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from config import Settings
from exact_features import _to_portable_frame, latest_exact_matrix
from engine import DataIntegrityError, PaperTradeEngine
from model_registry import ModelRegistry, load_scenarios
from candle_history import CandleHistoryStore


def synthetic_candles(rows: int = 1000) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=rows, freq="5min", tz="UTC")
    rng = np.random.default_rng(42)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.003, rows)))
    return pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0, 0.001, rows)),
            "high": close * (1 + rng.uniform(0.001, 0.009, rows)),
            "low": close * (1 - rng.uniform(0.001, 0.009, rows)),
            "close": close,
            "volume": rng.lognormal(8, 1, rows),
            "quote_volume": rng.lognormal(13, 1, rows),
            "close_time": np.array([int(ts.timestamp() * 1000) for ts in idx]) + 299_999,
            "trade_count": rng.integers(100, 8000, rows),
            "taker_buy_base_volume": rng.lognormal(7, 1, rows),
            "taker_buy_quote_volume": rng.lognormal(12, 1, rows),
        },
        index=idx,
    )


def test_portable_timestamp_is_unix_milliseconds_across_pandas_resolutions():
    frame = synthetic_candles(rows=3)
    portable = _to_portable_frame(frame)
    expected = [int(ts.timestamp() * 1000) for ts in frame.index]
    assert portable["open_time"].tolist() == expected


def test_candle_history_roundtrip_and_upsert():
    with tempfile.TemporaryDirectory() as directory:
        store = CandleHistoryStore(Path(directory) / "history.db")
        original = synthetic_candles(rows=12)
        assert store.save("1h", "BTCUSDT", original) == 12
        revised = original.tail(1).copy()
        revised.loc[:, "close"] = 321.5
        store.save("1h", "BTCUSDT", revised)
        loaded = store.load("1h", "BTCUSDT")
        assert len(loaded) == 12
        assert loaded.index.equals(original.index)
        assert loaded.iloc[-1]["close"] == 321.5
        store.close()


def test_latest_matrix_still_returns_complete_live_contract():
    base = synthetic_candles()
    frames = {"BTCUSDT": base, "ETHUSDT": base.copy(), "ADAUSDT": base.copy()}
    matrix, missing = latest_exact_matrix(frames)
    assert len(matrix) == 3
    assert missing == 0


def test_registry_and_scenarios():
    registry = ModelRegistry(Settings().package_dir)
    scenarios = load_scenarios(Settings().package_dir, registry)
    assert len(registry.models) == 83
    assert len(registry.symbols) == 106
    assert len(scenarios) == 219
    assert sum(s.enabled for s in scenarios) == 219
    assert not [s for s in scenarios if s.blocked_reason]


def test_feature_contract_and_model_prediction():
    registry = ModelRegistry(Settings().package_dir)
    base = synthetic_candles()
    frames = {"BTCUSDT": base, "ETHUSDT": base.copy(), "ADAUSDT": base.copy()}
    frame, _ = latest_exact_matrix(frames)
    assert list(frame.columns) == registry.feature_order + ["symbol"]
    assert not np.isinf(frame[registry.feature_order].to_numpy(dtype=float)).any()
    key = next(iter(registry.models))
    result = registry.predict(key[0], key[1], frame)
    assert "BTCUSDT" in result


def test_horizon_ten_regression_uses_h5_h15_mean():
    registry = ModelRegistry(Settings().package_dir)
    base = synthetic_candles()
    frames = {"BTCUSDT": base, "ETHUSDT": base.copy(), "ADAUSDT": base.copy()}
    frame, _ = latest_exact_matrix(frames)
    for prefix in ("future_range", "realized_volatility"):
        target = f"{prefix}_10mum_pct"
        assert registry.can_predict("15m", target)
        actual = registry.predict("15m", target, frame)
        lower = registry.predict("15m", f"{prefix}_5mum_pct", frame)
        upper = registry.predict("15m", f"{prefix}_15mum_pct", frame)
        for symbol, value in actual.items():
            assert value == (lower[symbol] + upper[symbol]) / 2.0


def test_candle_gap_is_repaired_only_with_binance_data():
    step = pd.Timedelta(minutes=5)
    expected_last = pd.Timestamp.now(tz="UTC").floor("5min") - step
    index = pd.date_range(end=expected_last, periods=300, freq=step)
    complete = pd.DataFrame({"close": np.arange(300)}, index=index)
    broken = complete.drop(index[-20])

    class FakeClient:
        async def klines(self, *_args, **_kwargs):
            return complete.copy()

    class FakeDb:
        def __init__(self): self.events = []
        def event(self, *args): self.events.append(args)

    engine = PaperTradeEngine.__new__(PaperTradeEngine)
    engine.settings = SimpleNamespace(warmup_bars=1000, data_gap_warning_bars=1)
    engine.integrity_silent_pairs = set()
    engine.client = FakeClient()
    engine.db = FakeDb()
    repaired = asyncio.run(engine._ensure_complete_candles("5m", "BTCUSDT", broken))
    assert repaired.index.equals(complete.index)
    assert engine.db.events[-1][0:3] == (
        "warning", "data_integrity", "Large candle gap repaired"
    )


def test_unrepairable_candle_gap_blocks_data():
    step = pd.Timedelta(minutes=5)
    expected_last = pd.Timestamp.now(tz="UTC").floor("5min") - step
    index = pd.date_range(end=expected_last, periods=300, freq=step)
    broken = pd.DataFrame({"close": np.arange(299)}, index=index.delete(-20))

    class FakeClient:
        async def klines(self, *_args, **_kwargs): return broken.copy()
    class FakeDb:
        def event(self, *_args): pass

    engine = PaperTradeEngine.__new__(PaperTradeEngine)
    engine.settings = SimpleNamespace(warmup_bars=1000, data_gap_warning_bars=10)
    engine.integrity_silent_pairs = set()
    engine.client, engine.db = FakeClient(), FakeDb()
    try:
        asyncio.run(engine._ensure_complete_candles("5m", "BTCUSDT", broken))
    except DataIntegrityError:
        pass
    else:
        raise AssertionError("unrepairable candle gap was accepted")
