from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path


APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from config import Settings
from model_registry import ModelRegistry
from spot_demo import SpotDemoClient


async def main(smoke_trade: bool) -> None:
    settings = Settings()
    registry = ModelRegistry(settings.package_dir)
    client = SpotDemoClient(settings.spot_demo_rest_url, settings.binance_api_key, settings.binance_api_secret)
    try:
        await client.exchange_info()
        account = await client.account()
        rows = [{"paper_symbol": symbol, "spot_symbol": client.spot_symbol(symbol)} for symbol in registry.symbols]
        available = [row for row in rows if row["spot_symbol"]]
        missing = [row["paper_symbol"] for row in rows if not row["spot_symbol"]]
        aliases = [row for row in available if row["paper_symbol"] != row["spot_symbol"]]
        result: dict = {
            "demo_account_connected": bool(account.get("canTrade")),
            "usdt_balance": next(
                ({"free": item.get("free"), "locked": item.get("locked")} for item in account.get("balances", []) if item.get("asset") == "USDT"),
                {"free": None, "locked": None},
            ),
            "paper_symbols": len(rows),
            "spot_available": len(available),
            "spot_missing": missing,
            "aliases": aliases,
        }
        if smoke_trade:
            stamp = int(time.time())
            buy = await client.market_buy("BTCUSDT", settings.spot_demo_quote_usdt, f"ptsmokeb{stamp}")
            sell_qty = client.net_base_quantity(buy)
            sell = await client.market_sell("BTCUSDT", sell_qty, f"ptsmokes{stamp}")
            result["smoke_trade"] = {
                "symbol": "BTCUSDT",
                "quote_requested": settings.spot_demo_quote_usdt,
                "buy_status": buy.get("status"),
                "buy_order_id": buy.get("orderId"),
                "buy_quote": buy.get("cummulativeQuoteQty"),
                "sell_status": sell.get("status"),
                "sell_order_id": sell.get("orderId"),
                "sell_quantity": format(sell_qty, "f"),
                "sell_quote": sell.get("cummulativeQuoteQty"),
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-trade", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.smoke_trade))
