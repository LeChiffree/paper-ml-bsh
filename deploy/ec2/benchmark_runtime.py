from __future__ import annotations

import asyncio
import json
import os
import resource
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "paper_trade_app"
sys.path.insert(0, str(APP))

os.environ.setdefault("PT_DB_PATH", "/tmp/paper-ml-benchmark.db")
os.environ.setdefault("PT_LOG_PATH", "/tmp/paper-ml-benchmark.log")
os.environ.setdefault("PT_LIVE_ENABLED", "true")
os.environ.setdefault("BINANCE_API_KEY", "")
os.environ.setdefault("BINANCE_API_SECRET", "")

from config import Settings
from db import Database
from engine import PaperTradeEngine


def peak_rss_mib() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


async def main() -> None:
    started = time.perf_counter()
    settings = Settings()
    db = Database(settings.db_path)

    model_started = time.perf_counter()
    engine = PaperTradeEngine(settings, db)
    model_seconds = time.perf_counter() - model_started

    bootstrap_started = time.perf_counter()
    await engine.bootstrap()
    bootstrap_seconds = time.perf_counter() - bootstrap_started

    timeframes = [
        value.strip()
        for value in os.getenv("BENCHMARK_TIMEFRAMES", "5m").split(",")
        if value.strip()
    ]
    cycle_started = time.perf_counter()
    await asyncio.gather(*(engine.run_timeframe_cycle(value) for value in timeframes))
    cycle_seconds = time.perf_counter() - cycle_started

    result = {
        "models": len(engine.registry.models),
        "symbols": len(engine.registry.symbols),
        "coverage": engine.coverage,
        "model_load_seconds": round(model_seconds, 3),
        "bootstrap_seconds": round(bootstrap_seconds, 3),
        "timeframes": timeframes,
        "cycle_seconds": round(cycle_seconds, 3),
        "refresh_ms": {
            value: round(engine.refresh_ms.get(value) or 0, 1) for value in timeframes
        },
        "peak_rss_mib": round(peak_rss_mib(), 1),
        "total_seconds": round(time.perf_counter() - started, 3),
        "last_error": engine.last_error,
    }
    print("BENCHMARK_RESULT=" + json.dumps(result, ensure_ascii=False))
    await engine.stop()


if __name__ == "__main__":
    asyncio.run(main())
