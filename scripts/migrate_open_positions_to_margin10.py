from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


APP = Path(__file__).resolve().parents[1] / "paper_trade_app"
sys.path.insert(0, str(APP))

from config import Settings  # noqa: E402
from execution_accounts import EXECUTION_ACCOUNTS  # noqa: E402
from futures_demo import FuturesDemoClient  # noqa: E402


def backup_database(db_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = db_path.with_name(f"paper_trade_before_margin10_{stamp}.db")
    with sqlite3.connect(db_path) as source, sqlite3.connect(target) as backup:
        source.backup(backup)
    return target


def resize_paper(conn: sqlite3.Connection, margin: float) -> int:
    rows = conn.execute(
        """SELECT account_position_id,account_id,leverage,entry_price,status
           FROM execution_account_positions WHERE status IN ('PENDING','OPEN')"""
    ).fetchall()
    for row_id, account_id, leverage, entry_price, status in rows:
        exposure = margin * int(leverage)
        values: list[object] = [margin, exposure]
        sql = "UPDATE execution_account_positions SET margin_usdt=?,exposure_usdt=?"
        if status == "OPEN" and entry_price:
            account = EXECUTION_ACCOUNTS[account_id]
            quantity = exposure / float(entry_price)
            entry_fee = exposure * float(account["fee_rate"])
            liquidation = None
            if account["market"] == "FUTURES":
                liquidation = float(entry_price) * (1 - 1 / int(leverage) + 0.005)
            sql += ",quantity=?,entry_fee=?,liquidation_price=?"
            values.extend([quantity, entry_fee, liquidation])
        sql += ",updated_at=? WHERE account_position_id=?"
        values.extend([datetime.now(timezone.utc).isoformat(), row_id])
        conn.execute(sql, values)
    conn.commit()
    return len(rows)


async def resize_demo(conn: sqlite3.Connection, settings: Settings) -> list[dict]:
    rows = conn.execute(
        """SELECT m.mirror_id,m.account_position_id,m.symbol,m.quantity,m.entry_order_id
           FROM futures_demo_mirrors m
           JOIN execution_account_positions e USING(account_position_id)
           WHERE m.status='OPEN' AND e.status='OPEN' AND e.account_id='futures_10x'"""
    ).fetchall()
    client = FuturesDemoClient(
        settings.futures_demo_rest_url,
        settings.futures_10x_api_key,
        settings.futures_10x_api_secret,
    )
    results = []
    try:
        account = await client.initialize()
        if not account.get("canTrade"):
            raise RuntimeError("Futures Demo trading disabled")
        for mirror_id, account_position_id, symbol, old_quantity, old_order_id in rows:
            stamp = int(time.time() * 1000)
            try:
                close = await client.market_close(
                    symbol, Decimal(str(old_quantity)), f"mlrzx{account_position_id}{stamp}"[:36]
                )
            except Exception as exc:
                results.append({"symbol": symbol, "status": "close_failed", "error": str(exc)})
                continue
            try:
                opened = await client.market_buy_leveraged_margin(
                    symbol, settings.motorlab_demo_margin_usdt, 10,
                    f"mlrze{account_position_id}{stamp}"[:36],
                )
                quantity = opened.get("executedQty") or opened["requestedQuantity"]
                conn.execute(
                    """UPDATE futures_demo_mirrors SET margin_usdt=?,exposure_usdt=?,
                    quantity=?,entry_order_id=?,entry_avg_price=?,exit_order_id=NULL,
                    exit_avg_price=NULL,error=?,updated_at=? WHERE mirror_id=?""",
                    (
                        settings.motorlab_demo_margin_usdt,
                        settings.motorlab_demo_margin_usdt * 10,
                        quantity, str(opened.get("orderId")), str(opened.get("avgPrice") or ""),
                        f"resized_from_order:{old_order_id};close_order:{close.get('orderId')}",
                        datetime.now(timezone.utc).isoformat(), mirror_id,
                    ),
                )
                conn.commit()
                results.append({
                    "symbol": symbol, "status": "resized", "old_quantity": old_quantity,
                    "new_quantity": quantity, "close_order": close.get("orderId"),
                    "open_order": opened.get("orderId"),
                })
            except Exception as exc:
                conn.execute(
                    "UPDATE futures_demo_mirrors SET status='RESIZE_OPEN_ERROR',error=?,updated_at=? WHERE mirror_id=?",
                    (str(exc), datetime.now(timezone.utc).isoformat(), mirror_id),
                )
                conn.commit()
                results.append({"symbol": symbol, "status": "open_failed_after_close", "error": str(exc)})
    finally:
        await client.close()
    return results


async def main() -> None:
    settings = Settings()
    backup = backup_database(settings.db_path)
    with sqlite3.connect(settings.db_path) as conn:
        demo = await resize_demo(conn, settings)
        paper_count = resize_paper(conn, settings.execution_account_margin_usdt)
    print(json.dumps({"backup": str(backup), "paper_rows": paper_count, "demo": demo}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
