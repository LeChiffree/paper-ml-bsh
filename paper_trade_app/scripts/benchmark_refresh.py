from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path


APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from binance_client import BinanceClient
from config import Settings


async def main() -> None:
    settings = Settings()
    symbols = json.loads((APP / "coin_universe.json").read_text(encoding="utf-8"))
    client = BinanceClient(
        settings.binance_rest_url,
        settings.binance_ws_url,
        settings.request_concurrency,
    )
    started = time.perf_counter()
    try:
        results = await asyncio.gather(
            *(client.klines(symbol, "5m", limit=3) for symbol in symbols),
            return_exceptions=True,
        )
    finally:
        await client.close()
    print(json.dumps({
        "symbols": len(symbols),
        "elapsed_s": round(time.perf_counter() - started, 3),
        "success": sum(not isinstance(item, Exception) for item in results),
        "failures": sum(isinstance(item, Exception) for item in results),
        "requests": client.stats.requests,
        "used_weight": client.stats.last_used_weight,
    }))


if __name__ == "__main__":
    asyncio.run(main())
