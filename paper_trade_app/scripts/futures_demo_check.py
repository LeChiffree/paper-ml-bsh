from __future__ import annotations

import asyncio
import argparse
import json
import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from config import Settings
from futures_demo import FuturesDemoClient

MISSING_SPOT = ["LITUSDT", "VVVUSDT", "AKEUSDT", "USELESSUSDT", "UAIUSDT", "FLOCKUSDT", "XMRUSDT", "HYPEUSDT", "RAYSOLUSDT", "PONSUSDT"]

async def main(smoke_trade: bool = False, close_five_x: bool = False) -> None:
    settings = Settings()
    client = FuturesDemoClient(settings.futures_demo_rest_url, settings.binance_api_key, settings.binance_api_secret)
    try:
        account = await client.initialize()
        open_positions = [
            {"symbol": item.get("symbol"), "positionAmt": item.get("positionAmt"),
             "notional": item.get("notional"), "leverage": item.get("leverage"),
             "isolated": item.get("isolated")}
            for item in account.get("positions", []) if float(item.get("positionAmt") or 0) != 0
        ]
        result = {"connected": bool(account.get("canTrade")), "open_positions": open_positions,
                  "available": [s for s in MISSING_SPOT if client.supports(s)],
                  "unavailable": [s for s in MISSING_SPOT if not client.supports(s)]}
        if smoke_trade:
            buy = await client.market_buy_notional("VVVUSDT", settings.futures_demo_quote_usdt, "ptfuturescheck")
            from decimal import Decimal
            qty = Decimal(buy.get("executedQty") or buy["requestedQuantity"])
            sell = await client.market_close("VVVUSDT", qty, "ptfuturesclose")
            result["smoke_trade"] = {"symbol": "VVVUSDT", "leverage": 1, "buy_status": buy.get("status"), "buy_order_id": buy.get("orderId"), "quantity": format(qty, "f"), "sell_status": sell.get("status"), "sell_order_id": sell.get("orderId"), "reduce_only": not client.hedge_mode}
        if close_five_x:
            from decimal import Decimal
            targets = [item for item in account.get("positions", [])
                       if Decimal(item.get("positionAmt") or "0") != 0 and int(item.get("leverage") or 0) == 5]
            closed, failures = [], []
            stamp = int(time.time())
            for index, item in enumerate(targets):
                try:
                    amount = Decimal(item["positionAmt"])
                    order = await client.market_flatten(item["symbol"], amount, f"ptflat5x{stamp}{index}")
                    closed.append({"symbol": item["symbol"], "positionAmt": item["positionAmt"],
                                   "side": "SELL" if amount > 0 else "BUY", "orderId": order.get("orderId"),
                                   "status": order.get("status")})
                except Exception as exc:
                    failures.append({"symbol": item.get("symbol"), "error": f"{type(exc).__name__}: {exc}"})
            refreshed = await client._request("GET", "/fapi/v2/account", signed=True)
            remaining = [{"symbol": item.get("symbol"), "positionAmt": item.get("positionAmt"),
                          "leverage": item.get("leverage")}
                         for item in refreshed.get("positions", [])
                         if float(item.get("positionAmt") or 0) != 0 and int(item.get("leverage") or 0) == 5]
            result["close_five_x"] = {"target_count": len(targets), "closed": closed,
                                      "failures": failures, "remaining": remaining}
        print(json.dumps(result, ensure_ascii=True, indent=2))
    finally:
        await client.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-trade", action="store_true")
    parser.add_argument("--close-5x", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.smoke_trade, args.close_5x))
