from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

import aiohttp
import pandas as pd


LOG = logging.getLogger(__name__)


KLINE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trade_count",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "ignore",
]


def rows_to_frame(rows: list[list]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=KLINE_COLUMNS).set_index("open_time")
    frame = pd.DataFrame(rows, columns=KLINE_COLUMNS)
    frame["open_time"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
    numeric = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "trade_count",
        "taker_buy_base_volume",
        "taker_buy_quote_volume",
    ]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.set_index("open_time").sort_index()


@dataclass
class RestStats:
    requests: int = 0
    failures: int = 0
    last_latency_ms: float = 0
    last_used_weight: str | None = None


class BinanceClient:
    def __init__(self, rest_url: str, ws_url: str, concurrency: int = 12):
        self.rest_url = rest_url.rstrip("/")
        self.ws_url = ws_url.rstrip("/")
        self.semaphore = asyncio.Semaphore(concurrency)
        self.session: aiohttp.ClientSession | None = None
        self.stats = RestStats()

    async def start(self) -> None:
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            self.session = aiohttp.ClientSession(timeout=timeout)

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def _get(self, path: str, params: dict) -> object:
        await self.start()
        assert self.session is not None
        async with self.semaphore:
            for attempt in range(5):
                started = time.perf_counter()
                try:
                    async with self.session.get(
                        f"{self.rest_url}{path}", params=params
                    ) as response:
                        self.stats.requests += 1
                        self.stats.last_latency_ms = (time.perf_counter() - started) * 1000
                        self.stats.last_used_weight = response.headers.get(
                            "X-MBX-USED-WEIGHT-1M"
                        )
                        if response.status in {418, 429}:
                            retry = int(response.headers.get("Retry-After", "5"))
                            await asyncio.sleep(min(retry, 60))
                            continue
                        response.raise_for_status()
                        return await response.json()
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    self.stats.failures += 1
                    if attempt == 4:
                        raise
                    LOG.warning("Binance request failed: %s", exc)
                    await asyncio.sleep((2**attempt) + random.random())
        raise RuntimeError("Binance request retry exhausted")

    async def klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 1000,
        start_time: int | None = None,
        end_time: int | None = None,
        closed_only: bool = True,
    ) -> pd.DataFrame:
        params: dict[str, object] = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        rows = await self._get("/fapi/v1/klines", params)
        frame = rows_to_frame(rows)  # type: ignore[arg-type]
        if closed_only and not frame.empty:
            now_ms = int(time.time() * 1000)
            frame = frame[frame["close_time"] < now_ms]
        return frame

    async def historical_klines(
        self,
        symbol: str,
        interval: str,
        start_time: int,
        end_time: int | None = None,
    ) -> pd.DataFrame:
        """Download every closed candle from start_time using Binance pages."""
        end_time = end_time or int(time.time() * 1000)
        cursor = start_time
        pages: list[pd.DataFrame] = []
        while cursor < end_time:
            page = await self.klines(
                symbol, interval, limit=1500, start_time=cursor,
                end_time=end_time, closed_only=True,
            )
            if page.empty:
                break
            pages.append(page)
            next_cursor = int(page.index[-1].timestamp() * 1000) + 1
            if next_cursor <= cursor:
                raise RuntimeError(f"Historical pagination stalled: {symbol} {interval}")
            cursor = next_cursor
            if len(page) < 1500:
                break
        if not pages:
            return pd.DataFrame(columns=KLINE_COLUMNS).set_index("open_time")
        result = pd.concat(pages)
        return result[~result.index.duplicated(keep="last")].sort_index()

    async def server_time(self) -> int:
        payload = await self._get("/fapi/v1/time", {})
        return int(payload["serverTime"])  # type: ignore[index]

    async def quote_volumes_24h(self) -> dict[str, float]:
        rows = await self._get("/fapi/v1/ticker/24hr", {})
        return {
            str(row["symbol"]): float(row.get("quoteVolume") or 0)
            for row in rows  # type: ignore[union-attr]
        }

    async def funding_rates(
        self, symbol: str, start_time: int, end_time: int
    ) -> list[dict[str, float | int]]:
        rows = await self._get(
            "/fapi/v1/fundingRate",
            {"symbol": symbol, "startTime": start_time, "endTime": end_time, "limit": 1000},
        )
        return [
            {"fundingTime": int(row["fundingTime"]), "fundingRate": float(row["fundingRate"])}
            for row in rows  # type: ignore[union-attr]
        ]

    async def stream_closed_1m(
        self,
        symbols: list[str],
        on_candle: Callable[[str, dict], Awaitable[None]],
        stop_event: asyncio.Event,
    ) -> None:
        streams = "/".join(f"{symbol.lower()}@kline_1m" for symbol in symbols)
        url = f"{self.ws_url}?streams={streams}"
        while not stop_event.is_set():
            await self.start()
            assert self.session is not None
            try:
                LOG.info("Connecting Binance 1m stream for %s symbols", len(symbols))
                async with self.session.ws_connect(
                    url, heartbeat=150, receive_timeout=240, max_msg_size=8_000_000
                ) as ws:
                    async for message in ws:
                        if stop_event.is_set():
                            break
                        if message.type == aiohttp.WSMsgType.TEXT:
                            payload = json.loads(message.data)
                            data = payload.get("data", payload)
                            kline = data.get("k", {})
                            if not kline.get("x"):
                                continue
                            symbol = str(data.get("s", "")).upper()
                            candle = {
                                "open_time": int(kline["t"]),
                                "close_time": int(kline["T"]),
                                "open": float(kline["o"]),
                                "high": float(kline["h"]),
                                "low": float(kline["l"]),
                                "close": float(kline["c"]),
                                "volume": float(kline["v"]),
                            }
                            await on_candle(symbol, candle)
                        elif message.type in {
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR,
                        }:
                            break
            except Exception as exc:  # reconnect loop is deliberate
                LOG.exception("Binance websocket disconnected: %s", exc)
            if not stop_event.is_set():
                await asyncio.sleep(3)
