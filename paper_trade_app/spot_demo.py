from __future__ import annotations

import hashlib
import hmac
import time
from decimal import Decimal, ROUND_DOWN
from typing import Any
from urllib.parse import urlencode

import aiohttp


class SpotDemoError(RuntimeError):
    pass


class SpotDemoClient:
    def __init__(self, base_url: str, api_key: str, api_secret: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret.encode("utf-8")
        self.session: aiohttp.ClientSession | None = None
        self.time_offset_ms = 0
        self.symbols: dict[str, dict[str, Any]] = {}

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    async def start(self) -> None:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def _request(self, method: str, path: str, params: dict[str, Any] | None = None, signed: bool = False) -> Any:
        await self.start()
        assert self.session is not None
        payload = dict(params or {})
        headers: dict[str, str] = {}
        if signed:
            if not self.configured:
                raise SpotDemoError("Demo Spot API credentials are not configured")
            payload.setdefault("recvWindow", 5000)
            payload["timestamp"] = int(time.time() * 1000) + self.time_offset_ms
            query = urlencode(payload)
            payload["signature"] = hmac.new(self.api_secret, query.encode("utf-8"), hashlib.sha256).hexdigest()
            headers["X-MBX-APIKEY"] = self.api_key
        async with self.session.request(method, f"{self.base_url}{path}", params=payload, headers=headers) as response:
            data = await response.json(content_type=None)
            if response.status >= 400:
                raise SpotDemoError(f"Spot Demo HTTP {response.status}: {data.get('code')} {data.get('msg')}")
            return data

    async def sync_time(self) -> int:
        data = await self._request("GET", "/api/v3/time")
        self.time_offset_ms = int(data["serverTime"]) - int(time.time() * 1000)
        return self.time_offset_ms

    async def exchange_info(self) -> dict[str, Any]:
        data = await self._request("GET", "/api/v3/exchangeInfo")
        self.symbols = {item["symbol"]: item for item in data.get("symbols", [])}
        return data

    async def account(self) -> dict[str, Any]:
        await self.sync_time()
        return await self._request("GET", "/api/v3/account", signed=True)

    async def ticker_prices(self) -> dict[str, float]:
        rows = await self._request("GET", "/api/v3/ticker/price")
        return {row["symbol"]: float(row["price"]) for row in rows}

    async def order(self, **params: Any) -> dict[str, Any]:
        return await self._request("POST", "/api/v3/order", params=params, signed=True)

    async def market_buy(self, symbol: str, quote_usdt: float, client_id: str) -> dict[str, Any]:
        return await self.order(symbol=symbol, side="BUY", type="MARKET", quoteOrderQty=f"{quote_usdt:.8f}", newClientOrderId=client_id, newOrderRespType="FULL")

    async def market_sell(self, symbol: str, quantity: Decimal, client_id: str) -> dict[str, Any]:
        return await self.order(symbol=symbol, side="SELL", type="MARKET", quantity=format(quantity, "f"), newClientOrderId=client_id, newOrderRespType="FULL")

    def spot_symbol(self, paper_symbol: str) -> str | None:
        if paper_symbol in self.symbols and self._tradable(self.symbols[paper_symbol]):
            return paper_symbol
        if paper_symbol.startswith("1000"):
            candidate = paper_symbol[4:]
            if candidate in self.symbols and self._tradable(self.symbols[candidate]):
                return candidate
        return None

    @staticmethod
    def _tradable(info: dict[str, Any]) -> bool:
        return info.get("status") == "TRADING" and info.get("quoteAsset") == "USDT" and info.get("isSpotTradingAllowed", True)

    def floor_quantity(self, symbol: str, quantity: Decimal) -> Decimal:
        info = self.symbols[symbol]
        lots = {item["filterType"]: item for item in info["filters"] if item["filterType"] in {"MARKET_LOT_SIZE", "LOT_SIZE"}}
        lot = lots.get("MARKET_LOT_SIZE")
        if lot is None or Decimal(lot["stepSize"]) <= 0:
            lot = lots["LOT_SIZE"]
        step = Decimal(lot["stepSize"])
        return (quantity / step).to_integral_value(rounding=ROUND_DOWN) * step

    def net_base_quantity(self, order: dict[str, Any]) -> Decimal:
        base = self.symbols[order["symbol"]]["baseAsset"]
        qty = Decimal(order["executedQty"])
        commission = sum((Decimal(fill["commission"]) for fill in order.get("fills", []) if fill.get("commissionAsset") == base), Decimal("0"))
        return self.floor_quantity(order["symbol"], qty - commission)
