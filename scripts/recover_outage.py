"""Apply an audited outage replay, then settle positions with historical 1m candles.

Run only while the live service is stopped. The audit JSON must come from
scripts/replay_audit.py and include model scores for every missed signal.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "paper_trade_app"
sys.path.insert(0, str(APP))

from binance_client import BinanceClient  # noqa: E402
from config import TIMEFRAME_MINUTES, Settings  # noqa: E402
from db import Database, utc_now  # noqa: E402
from engine import PaperTradeEngine  # noqa: E402
from execution_accounts import EXECUTION_ACCOUNTS  # noqa: E402


def to_ms(value: str) -> int:
    return int(pd.Timestamp(value).timestamp() * 1000)


def insert_audited_signals(settings: Settings, audit: dict) -> dict[str, int]:
    missed = audit.get("missed", [])
    if int(audit.get("totals", {}).get("missed", -1)) != len(missed):
        raise RuntimeError("Audit total does not match missed signal rows")
    active_ids = settings.active_scenario_ids()
    unknown = sorted({int(row["scenario_id"]) for row in missed} - active_ids)
    if unknown:
        raise RuntimeError(f"Audit includes inactive scenarios: {unknown}")

    now = utc_now()
    inserted_positions = 0
    inserted_signals = 0
    inserted_accounts = 0
    with sqlite3.connect(settings.db_path, timeout=60) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        scenarios = {
            int(row["scenario_id"]): row
            for row in conn.execute(
                "SELECT * FROM scenarios WHERE scenario_id IN (%s)"
                % ",".join("?" for _ in active_ids),
                sorted(active_ids),
            )
        }
        conn.execute("BEGIN IMMEDIATE")
        for item in missed:
            scenario_id = int(item["scenario_id"])
            spec = scenarios[scenario_id]
            signal_time = to_ms(item["signal_time"])
            timeframe = str(spec["timeframe"])
            entry_time = signal_time + TIMEFRAME_MINUTES[timeframe] * 60_000
            deadline = entry_time + int(spec["horizon"]) * TIMEFRAME_MINUTES[timeframe] * 60_000
            symbol = str(item["symbol"])
            before = conn.total_changes
            conn.execute(
                """INSERT OR IGNORE INTO signals(
                scenario_id,symbol,timeframe,signal_time,decision,reason,tp_score,
                range_prediction,volatility_prediction,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    scenario_id, symbol, timeframe, signal_time, "accepted", "outage_replay",
                    item.get("tp_score"), item.get("range_prediction"),
                    item.get("volatility_prediction"), now,
                ),
            )
            inserted_signals += conn.total_changes - before
            before = conn.total_changes
            cursor = conn.execute(
                """INSERT OR IGNORE INTO positions(
                scenario_id,symbol,timeframe,target,signal_time,entry_time,deadline,status,
                notional,tp_score,range_prediction,volatility_prediction,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    scenario_id, symbol, timeframe, spec["target"], signal_time,
                    entry_time, deadline, "PENDING", settings.trade_notional_tl,
                    item.get("tp_score"), item.get("range_prediction"),
                    item.get("volatility_prediction"), now, now,
                ),
            )
            changed = conn.total_changes - before
            inserted_positions += changed
            if changed:
                position_id = int(cursor.lastrowid)
            else:
                position_id = int(conn.execute(
                    "SELECT position_id FROM positions WHERE scenario_id=? AND symbol=? AND signal_time=?",
                    (scenario_id, symbol, signal_time),
                ).fetchone()[0])
            for account_id, config in EXECUTION_ACCOUNTS.items():
                before = conn.total_changes
                margin = settings.execution_account_margin_usdt
                conn.execute(
                    """INSERT OR IGNORE INTO execution_account_positions(
                    position_id,account_id,market_type,leverage,scenario_id,symbol,timeframe,
                    status,signal_time,entry_time,deadline,margin_usdt,exposure_usdt,
                    created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        position_id, account_id, config["market"], config["leverage"],
                        scenario_id, symbol, timeframe, "PENDING", signal_time,
                        entry_time, deadline, margin, margin * config["leverage"], now, now,
                    ),
                )
                inserted_accounts += conn.total_changes - before
        conn.commit()
    return {
        "signals": inserted_signals,
        "positions": inserted_positions,
        "account_positions": inserted_accounts,
    }


async def download_1m(client: BinanceClient, symbol: str, start_ms: int, end_ms: int):
    last_error = None
    for attempt in range(6):
        try:
            frame = await client.historical_klines(symbol, "1m", start_ms, end_time=end_ms)
            await asyncio.sleep(0.35)
            return frame
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(min(45, 2 ** (attempt + 1)))
    raise RuntimeError(f"1m recovery failed for {symbol}") from last_error


async def replay_positions(settings: Settings, start_ms: int, end_ms: int) -> dict:
    db = Database(settings.db_path)
    engine = PaperTradeEngine(settings, db)
    # Explicit defense-in-depth: outage recovery must never place API orders.
    if settings.motorlab_demo_enabled or settings.legacy_demo_enabled:
        raise RuntimeError("Demo/API execution must be disabled during recovery")
    symbols = [
        row["symbol"] for row in db.query(
            "SELECT DISTINCT symbol FROM positions WHERE status IN ('PENDING','OPEN') ORDER BY symbol"
        )
    ]
    client = BinanceClient(settings.binance_rest_url, settings.binance_ws_url, concurrency=1)
    processed_candles = 0
    try:
        for index, symbol in enumerate(symbols, 1):
            frame = await download_1m(client, symbol, start_ms, end_ms)
            for open_time, row in frame.iterrows():
                candle = {
                    "open_time": int(pd.Timestamp(open_time).timestamp() * 1000),
                    "close_time": int(row["close_time"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                }
                await engine.on_1m_candle(symbol, candle)
                processed_candles += 1
            print(f"replayed {index}/{len(symbols)} {symbol} candles={len(frame)}", flush=True)
    finally:
        await client.close()
        await engine.client.close()
        for demo_client in engine.futures_clients.values():
            await demo_client.close()
        await engine.spot_client.close()
        if engine.history_store is not None:
            engine.history_store.close()
    counts = {
        row["status"]: row["count"]
        for row in db.query("SELECT status,COUNT(*) count FROM positions GROUP BY status")
    }
    return {"symbols": len(symbols), "candles": processed_candles, "position_counts": counts}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--insert-only", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not args.apply:
        raise SystemExit("Refusing mutation without --apply")
    os.environ["PT_MOTORLAB_DEMO_ENABLED"] = "false"
    os.environ["PT_LEGACY_DEMO_ENABLED"] = "false"
    settings = Settings()
    audit = json.loads(Path(args.audit).read_text(encoding="utf-8"))
    inserted = insert_audited_signals(settings, audit)
    replay = None if args.insert_only else asyncio.run(
        replay_positions(settings, to_ms(args.start), to_ms(args.end))
    )
    result = {
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "period": {"start": args.start, "end": args.end},
        "inserted": inserted,
        "replay": replay,
    }
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
