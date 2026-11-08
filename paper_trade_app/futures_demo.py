from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from decimal import Decimal, ROUND_DOWN
from typing import Any
from urllib.parse import urlencode

import aiohttp


class FuturesDemoError(RuntimeError):
    pass


class FuturesDemoClient:
    def __init__(self, base_url: str, api_key: str, api_secret: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret.encode()
        self.session: aiohttp.ClientSession | None = None
        self.time_offset_ms = 0
        self.symbols: dict[str, dict[str, Any]] = {}
        self.leverage_set: set[str] = set()
        self.isolated_set: set[str] = set()
        self.hedge_mode = False

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
        headers = {}
        if signed:
            payload.setdefault("recvWindow", 5000)
            payload["timestamp"] = int(time.time() * 1000) + self.time_offset_ms
            query = urlencode(payload)
            payload["signature"] = hmac.new(self.api_secret, query.encode(), hashlib.sha256).hexdigest()
            headers["X-MBX-APIKEY"] = self.api_key
        async with self.session.request(method, f"{self.base_url}{path}", params=payload, headers=headers) as response:
            data = await response.json(content_type=None)
            if response.status >= 400:
                raise FuturesDemoError(f"Futures Demo HTTP {response.status}: {data.get('code')} {data.get('msg')}")
            return data

    async def initialize(self) -> dict[str, Any]:
        server, info = await self._request("GET", "/fapi/v1/time"), await self._request("GET", "/fapi/v1/exchangeInfo")
        self.time_offset_ms = int(server["serverTime"]) - int(time.time() * 1000)
        self.symbols = {item["symbol"]: item for item in info.get("symbols", [])}
        account = await self._request("GET", "/fapi/v2/account", signed=True)
        mode = await self._request("GET", "/fapi/v1/positionSide/dual", signed=True)
        self.hedge_mode = bool(mode.get("dualSidePosition"))
        return account

    async def account(self) -> dict[str, Any]:
        return await self._request("GET", "/fapi/v2/account", signed=True)

    def supports(self, symbol: str) -> bool:
        item = self.symbols.get(symbol, {})
        return item.get("status") == "TRADING" and item.get("quoteAsset") == "USDT" and item.get("contractType") == "PERPETUAL"

    async def ticker_prices(self) -> dict[str, float]:
        rows = await self._request("GET", "/fapi/v1/ticker/price")
        return {row["symbol"]: float(row["price"]) for row in rows}

    async def order_status(self, symbol: str, order_id: str | int) -> dict[str, Any]:
        return await self._request(
            "GET", "/fapi/v1/order", {"symbol": symbol, "orderId": order_id}, signed=True
        )

    async def _filled_order(self, symbol: str, order: dict[str, Any]) -> dict[str, Any]:
        """Binance may briefly return avgPrice=0 for a completed market order."""
        if Decimal(str(order.get("avgPrice") or "0")) > 0:
            return order
        for delay in (0.2, 0.5, 1.0):
            await asyncio.sleep(delay)
            refreshed = await self.order_status(symbol, order["orderId"])
            order.update(refreshed)
            if Decimal(str(order.get("avgPrice") or "0")) > 0:
                break
        return order

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        # Apply this before every entry. The account can also be changed outside
        # this process, so a process-local cache is not a sufficient guarantee.
        await self._request(
            "POST", "/fapi/v1/leverage",
            {"symbol": symbol, "leverage": int(leverage)}, signed=True,
        )
        self.leverage_set.add(symbol)

    async def set_leverage_one(self, symbol: str) -> None:
        await self.set_leverage(symbol, 1)

    async def set_isolated(self, symbol: str) -> None:
        try:
            await self._request(
                "POST", "/fapi/v1/marginType",
                {"symbol": symbol, "marginType": "ISOLATED"}, signed=True,
            )
        except FuturesDemoError as exc:
            # Binance returns -4046 when the requested margin type is already set.
            if "-4046" not in str(exc):
                raise
        self.isolated_set.add(symbol)

    def floor_quantity(self, symbol: str, quantity: Decimal) -> Decimal:
        filters = {f["filterType"]: f for f in self.symbols[symbol]["filters"]}
        lot = filters.get("MARKET_LOT_SIZE") or filters["LOT_SIZE"]
        step = Decimal(lot["stepSize"])
        return (quantity / step).to_integral_value(rounding=ROUND_DOWN) * step

    async def market_buy_notional(self, symbol: str, quote_usdt: float, client_id: str) -> dict[str, Any]:
        if Decimal(str(quote_usdt)) != Decimal("10"):
            raise FuturesDemoError(f"Futures entries must be exactly 10 USDT, got {quote_usdt}")
        await self.set_isolated(symbol)
        await self.set_leverage_one(symbol)
        ticker = await self._request("GET", "/fapi/v1/ticker/price", {"symbol": symbol})
        quantity = self.floor_quantity(symbol, Decimal(str(quote_usdt)) / Decimal(ticker["price"]))
        if quantity <= 0:
            raise FuturesDemoError(f"10 USDT is below quantity step for {symbol}")
        params = {"symbol": symbol, "side": "BUY", "type": "MARKET", "quantity": format(quantity, "f"), "newClientOrderId": client_id, "newOrderRespType": "RESULT"}
        if self.hedge_mode:
            params["positionSide"] = "LONG"
        order = await self._request("POST", "/fapi/v1/order", params, signed=True)
        order = await self._filled_order(symbol, order)
        order["requestedQuantity"] = format(quantity, "f")
        return order

    async def market_buy_leveraged_notional(
        self, symbol: str, notional_usdt: float, leverage: int, client_id: str
    ) -> dict[str, Any]:
        if int(leverage) not in {2, 5, 10}:
            raise FuturesDemoError(
                f"MotorLab leverage must be 2x/5x/10x, got {leverage}x"
            )
        await self.set_isolated(symbol)
        await self.set_leverage(symbol, leverage)
        ticker = await self._request("GET", "/fapi/v1/ticker/price", {"symbol": symbol})
        notional = Decimal(str(notional_usdt))
        quantity = self.floor_quantity(symbol, notional / Decimal(ticker["price"]))
        if quantity <= 0:
            raise FuturesDemoError(f"{notional} USDT is below quantity step for {symbol}")
        params = {
            "symbol": symbol, "side": "BUY", "type": "MARKET",
            "quantity": format(quantity, "f"), "newClientOrderId": client_id,
            "newOrderRespType": "RESULT",
        }
        if self.hedge_mode:
            params["positionSide"] = "LONG"
        order = await self._request("POST", "/fapi/v1/order", params, signed=True)
        order = await self._filled_order(symbol, order)
        order["requestedQuantity"] = format(quantity, "f")
        return order

    async def market_buy_leveraged_margin(
        self, symbol: str, margin_usdt: float, leverage: int, client_id: str
    ) -> dict[str, Any]:
        if Decimal(str(margin_usdt)) != Decimal("10"):
            raise FuturesDemoError(
                f"MotorLab futures margin must be exactly 10 USDT, got {margin_usdt}"
            )
        notional = Decimal(str(margin_usdt)) * Decimal(int(leverage))
        return await self.market_buy_leveraged_notional(
            symbol, float(notional), leverage, client_id
        )

    async def market_close(self, symbol: str, quantity: Decimal, client_id: str) -> dict[str, Any]:
        params = {"symbol": symbol, "side": "SELL", "type": "MARKET", "quantity": format(quantity, "f"), "newClientOrderId": client_id, "newOrderRespType": "RESULT"}
        if self.hedge_mode:
            params["positionSide"] = "LONG"
        else:
            params["reduceOnly"] = "true"
        order = await self._request("POST", "/fapi/v1/order", params, signed=True)
        return await self._filled_order(symbol, order)

    async def market_flatten(self, symbol: str, position_amount: Decimal, client_id: str) -> dict[str, Any]:
        """Close exactly one existing long/short position without increasing exposure."""
        if position_amount == 0:
            raise FuturesDemoError(f"Position is already flat for {symbol}")
        params = {"symbol": symbol, "side": "SELL" if position_amount > 0 else "BUY",
                  "type": "MARKET", "quantity": format(abs(position_amount), "f"),
                  "newClientOrderId": client_id, "newOrderRespType": "RESULT"}
        if self.hedge_mode:
            params["positionSide"] = "LONG" if position_amount > 0 else "SHORT"
        else:
            params["reduceOnly"] = "true"
        return await self._request("POST", "/fapi/v1/order", params, signed=True)
