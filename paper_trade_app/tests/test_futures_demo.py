from __future__ import annotations

import asyncio
import sys
from decimal import Decimal
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from futures_demo import FuturesDemoClient, FuturesDemoError


def test_futures_support_and_quantity_rounding():
    client = FuturesDemoClient("https://demo-fapi.binance.com", "key", "secret")
    client.symbols = {"XUSDT": {"status": "TRADING", "quoteAsset": "USDT", "contractType": "PERPETUAL",
        "filters": [{"filterType": "LOT_SIZE", "stepSize": "0.1"}, {"filterType": "MARKET_LOT_SIZE", "stepSize": "1"}]}}
    assert client.supports("XUSDT")
    assert client.floor_quantity("XUSDT", Decimal("12.9")) == Decimal("12")


def test_futures_entry_forces_isolated_one_x_and_ten_usdt():
    client = FuturesDemoClient("https://demo-fapi.binance.com", "key", "secret")
    client.symbols = {"XUSDT": {"status": "TRADING", "quoteAsset": "USDT", "contractType": "PERPETUAL",
        "filters": [{"filterType": "MARKET_LOT_SIZE", "stepSize": "0.1"}]}}
    calls = []

    async def fake_request(method, path, params=None, signed=False):
        calls.append((method, path, params, signed))
        if path == "/fapi/v1/ticker/price":
            return {"price": "2"}
        if path == "/fapi/v1/order":
            return {"orderId": 1, "status": "FILLED"}
        return {}

    client._request = fake_request
    order = asyncio.run(client.market_buy_notional("XUSDT", 10, "test-entry"))
    assert [call[1] for call in calls[:4]] == [
        "/fapi/v1/marginType", "/fapi/v1/leverage", "/fapi/v1/ticker/price", "/fapi/v1/order"
    ]
    assert [call[1] for call in calls[4:]] == ["/fapi/v1/order"] * 3
    assert calls[0][2]["marginType"] == "ISOLATED"
    assert calls[1][2]["leverage"] == 1
    assert calls[3][2]["quantity"] == "5"
    assert order["requestedQuantity"] == "5"


def test_futures_entry_rejects_non_ten_usdt():
    client = FuturesDemoClient("https://demo-fapi.binance.com", "key", "secret")
    try:
        asyncio.run(client.market_buy_notional("XUSDT", 11, "bad-entry"))
    except FuturesDemoError as exc:
        assert "exactly 10 USDT" in str(exc)
    else:
        raise AssertionError("non-10 USDT futures entry was accepted")


def test_motorlab_entry_forces_isolated_selected_leverage_and_ten_usdt_margin():
    client = FuturesDemoClient("https://demo-fapi.binance.com", "key", "secret")
    client.symbols = {"XUSDT": {"status": "TRADING", "quoteAsset": "USDT", "contractType": "PERPETUAL",
        "filters": [{"filterType": "MARKET_LOT_SIZE", "stepSize": "0.1"}]}}
    calls = []

    async def fake_request(method, path, params=None, signed=False):
        calls.append((method, path, params, signed))
        if path == "/fapi/v1/ticker/price":
            return {"price": "2"}
        if path == "/fapi/v1/order":
            return {"orderId": 2, "status": "FILLED", "avgPrice": "2"}
        return {}

    client._request = fake_request
    order = asyncio.run(client.market_buy_leveraged_margin("XUSDT", 10, 10, "ml10-test"))
    assert calls[0][1] == "/fapi/v1/marginType"
    assert calls[1][1] == "/fapi/v1/leverage"
    assert calls[1][2]["leverage"] == 10
    assert Decimal(calls[3][2]["quantity"]) == Decimal("50")
    assert Decimal(order["requestedQuantity"]) == Decimal("50")
