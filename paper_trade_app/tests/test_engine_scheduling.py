from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pandas as pd


APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from engine import PaperTradeEngine


def test_different_timeframes_run_concurrently():
    engine = PaperTradeEngine.__new__(PaperTradeEngine)
    engine._cycle_locks = {tf: asyncio.Lock() for tf in ("5m", "15m", "1h", "4h")}

    async def refresh(_timeframe: str) -> None:
        await asyncio.sleep(0.05)

    async def evaluate(_timeframe: str) -> None:
        await asyncio.sleep(0.05)

    engine.refresh_timeframe = refresh
    engine._evaluate_timeframe_unlocked = evaluate

    async def exercise() -> float:
        started = time.perf_counter()
        await asyncio.gather(
            engine.run_timeframe_cycle("5m"),
            engine.run_timeframe_cycle("15m"),
            engine.run_timeframe_cycle("1h"),
            engine.run_timeframe_cycle("4h"),
        )
        return time.perf_counter() - started

    assert asyncio.run(exercise()) < 0.25


def test_same_timeframe_remains_serialized():
    engine = PaperTradeEngine.__new__(PaperTradeEngine)
    engine._cycle_locks = {"1h": asyncio.Lock()}
    active = 0
    peak = 0

    async def refresh(_timeframe: str) -> None:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)

    async def evaluate(_timeframe: str) -> None:
        nonlocal active
        await asyncio.sleep(0.02)
        active -= 1

    engine.refresh_timeframe = refresh
    engine._evaluate_timeframe_unlocked = evaluate

    async def exercise() -> None:
        await asyncio.gather(
            engine.run_timeframe_cycle("1h"),
            engine.run_timeframe_cycle("1h"),
        )

    asyncio.run(exercise())
    assert peak == 1


def _policy_engine(failing_symbol: str) -> PaperTradeEngine:
    step = pd.Timedelta(minutes=5)
    end = pd.Timestamp.now(tz="UTC").floor("5min") - step
    frame = pd.DataFrame(
        {"close": range(300)},
        index=pd.date_range(end=end, periods=300, freq=step),
    )

    class Client:
        async def klines(self, symbol, *_args, **_kwargs):
            if symbol == failing_symbol:
                raise RuntimeError("missing candle")
            return frame.tail(3)

    class Db:
        def __init__(self): self.events = []
        def event(self, *args): self.events.append(args)

    engine = PaperTradeEngine.__new__(PaperTradeEngine)
    engine.registry = SimpleNamespace(symbols=["BTCUSDT", "ETHUSDT", "LOWUSDT"])
    engine.client, engine.db = Client(), Db()
    engine.settings = SimpleNamespace(warmup_bars=1000, data_gap_warning_bars=10)
    engine.candles = {("5m", symbol): frame.copy() for symbol in engine.registry.symbols}
    engine.coverage, engine.last_close = {"5m": 3}, {"5m": None}
    engine.integrity_silent_pairs = set()
    engine.critical_symbols = {"BTCUSDT", "ETHUSDT"}
    engine._excluded_symbols = {"5m": set()}
    engine.last_error = None

    async def no_refresh(*_args, **_kwargs): return None
    engine._refresh_critical_symbols = no_refresh
    return engine


def test_non_critical_missing_symbol_is_excluded_but_cycle_continues():
    engine = _policy_engine("LOWUSDT")
    assert asyncio.run(engine.refresh_timeframe("5m")) is True
    assert engine._excluded_symbols["5m"] == {"LOWUSDT"}


def test_critical_missing_symbol_blocks_timeframe():
    engine = _policy_engine("BTCUSDT")
    assert asyncio.run(engine.refresh_timeframe("5m")) is False
    assert engine._excluded_symbols["5m"] == {"BTCUSDT"}
    assert engine.db.events[-1][0:3] == (
        "error", "data_integrity", "5m cycle blocked: critical-volume candle missing"
    )


def test_runtime_stale_candle_never_triggers_full_history_download():
    step = pd.Timedelta(minutes=5)
    expected_last = pd.Timestamp.now(tz="UTC").floor("5min") - step
    stale = pd.DataFrame(
        {"close": range(300)},
        index=pd.date_range(end=expected_last-step, periods=300, freq=step),
    )

    class Client:
        historical_calls = 0
        recent_calls = 0
        async def klines(self, *_args, **_kwargs):
            self.recent_calls += 1
            return stale.tail(5)
        async def historical_klines(self, *_args, **_kwargs):
            self.historical_calls += 1
            raise AssertionError("runtime refresh requested full history")

    class Db:
        def __init__(self): self.events = []
        def event(self, *args): self.events.append(args)

    engine = PaperTradeEngine.__new__(PaperTradeEngine)
    engine.registry = SimpleNamespace(symbols=["BTCUSDT"])
    engine.client, engine.db = Client(), Db()
    engine.settings = SimpleNamespace(warmup_bars=1000, data_gap_warning_bars=10)
    engine.history_store = SimpleNamespace(save=lambda *_args: None)
    engine.candles = {("5m", "BTCUSDT"): stale.copy()}
    engine.coverage, engine.last_close = {"5m": 1}, {"5m": None}
    engine.integrity_silent_pairs = set()
    engine.critical_symbols = {"BTCUSDT"}
    engine._excluded_symbols = {"5m": set()}
    engine.last_error = None
    async def no_refresh(*_args, **_kwargs): return None
    engine._refresh_critical_symbols = no_refresh

    assert asyncio.run(engine.refresh_timeframe("5m")) is False
    assert engine.client.recent_calls == 2
    assert engine.client.historical_calls == 0
