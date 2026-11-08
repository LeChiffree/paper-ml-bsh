from __future__ import annotations

import sys
import asyncio
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace


APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from spot_demo import SpotDemoClient
from engine import PaperTradeEngine


def test_spot_alias_and_market_rounding():
    client = SpotDemoClient("https://demo-api.binance.com", "key", "secret")
    info = {
        "symbol": "SHIBUSDT", "baseAsset": "SHIB", "quoteAsset": "USDT",
        "status": "TRADING", "isSpotTradingAllowed": True,
        "filters": [
            {"filterType": "LOT_SIZE", "stepSize": "1"},
            {"filterType": "MARKET_LOT_SIZE", "stepSize": "10"},
        ],
    }
    client.symbols = {"SHIBUSDT": info}
    assert client.spot_symbol("1000SHIBUSDT") == "SHIBUSDT"
    assert client.floor_quantity("SHIBUSDT", Decimal("123")) == Decimal("120")


def test_non_spot_symbol_is_rejected():
    client = SpotDemoClient("https://demo-api.binance.com", "key", "secret")
    client.symbols = {"XUSDT": {"symbol": "XUSDT", "quoteAsset": "USDT", "status": "BREAK"}}
    assert client.spot_symbol("XUSDT") is None


def test_insufficient_spot_balance_falls_back_to_futures_one_x():
    class FakeSpot:
        def spot_symbol(self, symbol): return symbol
        async def market_buy(self, *_args, **_kwargs):
            raise RuntimeError("Spot Demo HTTP 400: -2010 Account has insufficient balance for requested action")

    class FakeFutures:
        def supports(self, _symbol): return True
        async def market_buy_notional(self, *_args, **_kwargs):
            return {"executedQty": "2", "avgPrice": "5", "orderId": 99}

    class FakeDb:
        def __init__(self): self.calls = []
        def execute(self, sql, params=()): self.calls.append((sql, params)); return 1
        def event(self, *args): self.calls.append(("event", args))

    engine = PaperTradeEngine.__new__(PaperTradeEngine)
    engine.spot_scenario_ids = {7}
    engine.spot_client = FakeSpot()
    engine.futures_client = FakeFutures()
    engine.spot_demo_connected = True
    engine.futures_demo_connected = True
    engine.settings = SimpleNamespace(spot_demo_quote_usdt=10.0, futures_demo_quote_usdt=10.0)
    engine.db = FakeDb()
    asyncio.run(engine._open_spot_demo({"position_id": 42, "scenario_id": 7, "symbol": "XUSDT"}))
    update = next(params for sql, params in engine.db.calls if "SET status='OPEN'" in sql)
    assert update[0] == "FUTURES_1X"
    assert update[1] == "XUSDT"
    assert update[5] == "spot_insufficient_balance_fallback"
