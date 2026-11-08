"""Settle PENDING/OPEN paper positions with historical Binance 1m candles."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "paper_trade_app"
sys.path.insert(0, str(APP))

from binance_client import BinanceClient  # noqa: E402
from config import Settings  # noqa: E402
from execution_accounts import EXECUTION_ACCOUNTS  # noqa: E402


SELECTED = {5, 9, 10, 15, 19, 49, 56, 64, 79, 108, 111}


def to_ms(value: str) -> int:
    return int(pd.Timestamp(value).timestamp() * 1000)


def merged_intervals(rows, end_ms: int):
    intervals = sorted(
        (int(row["entry_time"]), min(int(row["deadline"]), end_ms))
        for row in rows if int(row["entry_time"]) <= end_ms
    )
    merged = []
    for start, end in intervals:
        if not merged or start > merged[-1][1] + 60_000:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return merged


async def download_interval(client, symbol: str, start_ms: int, end_ms: int):
    cursor = start_ms
    pages = []
    while cursor <= end_ms:
        page = await client.klines(
            symbol, "1m", limit=1000, start_time=cursor, end_time=end_ms, closed_only=True
        )
        if page.empty:
            break
        pages.append(page)
        next_cursor = int(page.index[-1].timestamp() * 1000) + 1
        if next_cursor <= cursor:
            raise RuntimeError(f"Pagination stalled for {symbol}")
        cursor = next_cursor
        await asyncio.sleep(0.16)
        if len(page) < 1000:
            break
    if not pages:
        return pd.DataFrame()
    frame = pd.concat(pages)
    return frame[~frame.index.duplicated(keep="last")].sort_index()


def first_exit(frame, sl_price: float, tp_price: float, deadline: int):
    for open_time, candle in frame.iterrows():
        if float(candle["low"]) <= sl_price:
            return open_time, int(candle["close_time"]), sl_price, "stop"
        if float(candle["high"]) >= tp_price:
            return open_time, int(candle["close_time"]), tp_price, "take"
        if int(candle["close_time"]) >= deadline:
            return open_time, int(candle["close_time"]), float(candle["close"]), "time"
    return None


def funding_cost(rates, entry_time: int, exit_time: int, exposure: float) -> float:
    return exposure * sum(
        float(item["fundingRate"])
        for item in rates
        if entry_time <= int(item["fundingTime"]) <= exit_time
    )


def recompute(conn):
    for scenario_id in SELECTED:
        row = conn.execute(
            """SELECT COUNT(*),
            SUM(CASE WHEN exit_reason='take' THEN 1 ELSE 0 END),
            SUM(CASE WHEN exit_reason='stop' THEN 1 ELSE 0 END),
            SUM(CASE WHEN exit_reason='time' THEN 1 ELSE 0 END),
            COALESCE(SUM(net_pnl),0)
            FROM positions WHERE scenario_id=? AND status='CLOSED'""",
            (scenario_id,),
        ).fetchone()
        initial = float(conn.execute(
            "SELECT initial_cash FROM scenarios WHERE scenario_id=?", (scenario_id,)
        ).fetchone()[0])
        conn.execute(
            """UPDATE scenarios SET trade_count=?,win_count=?,loss_count=?,time_count=?,
            realized_pnl=?,cash=? WHERE scenario_id=?""",
            (int(row[0] or 0), int(row[1] or 0), int(row[2] or 0), int(row[3] or 0),
             float(row[4] or 0), initial + float(row[4] or 0), scenario_id),
        )


async def main_async(args):
    os.environ["PT_ACTIVE_SCENARIO_IDS"] = ",".join(map(str, sorted(SELECTED)))
    os.environ["PT_MOTORLAB_DEMO_ENABLED"] = "false"
    os.environ["PT_LEGACY_DEMO_ENABLED"] = "false"
    settings = Settings()
    end_ms = to_ms(args.end)
    client = BinanceClient(settings.binance_rest_url, settings.binance_ws_url, concurrency=1)
    completed = 0
    unresolved = []
    candle_rows = 0
    with sqlite3.connect(settings.db_path, timeout=60) as conn:
        conn.row_factory = sqlite3.Row
        positions = list(conn.execute(
            """SELECT p.*,s.tp_pct,s.sl_pct FROM positions p JOIN scenarios s USING(scenario_id)
            WHERE p.status IN ('PENDING','OPEN') AND p.scenario_id IN (%s)
            ORDER BY p.symbol,p.entry_time,p.position_id"""
            % ",".join("?" for _ in SELECTED), sorted(SELECTED)
        ))
        by_symbol = {}
        for row in positions:
            by_symbol.setdefault(row["symbol"], []).append(row)
        try:
            for index, (symbol, symbol_rows) in enumerate(sorted(by_symbol.items()), 1):
                parts = []
                intervals = merged_intervals(symbol_rows, end_ms)
                for start, end in intervals:
                    part = await download_interval(client, symbol, start, end)
                    if not part.empty:
                        parts.append(part)
                if not parts:
                    unresolved.extend(int(row["position_id"]) for row in symbol_rows)
                    continue
                candles = pd.concat(parts)
                candles = candles[~candles.index.duplicated(keep="last")].sort_index()
                # Pandas 2.x may preserve the exchange frame as datetime64[us].
                # Raw ``view('int64')`` therefore is not guaranteed to be
                # nanoseconds. Convert explicitly to Unix milliseconds so it
                # matches positions.entry_time/deadline.
                candle_open_ms = candles.index.to_numpy(dtype="datetime64[ms]").astype("int64")
                candle_rows += len(candles)
                try:
                    rates = await client.funding_rates(
                        symbol, min(int(row["entry_time"]) for row in symbol_rows), end_ms
                    )
                    await asyncio.sleep(0.16)
                    funding_error = None
                except Exception as exc:
                    rates = []
                    funding_error = f"historical_rebuild:{type(exc).__name__}:{exc}"

                for original in symbol_rows:
                    row = dict(original)
                    entry_time = int(row["entry_time"])
                    window = candles[
                        (candle_open_ms >= entry_time)
                        & (candle_open_ms <= min(int(row["deadline"]), end_ms))
                    ]
                    if window.empty:
                        unresolved.append(int(row["position_id"]))
                        continue
                    if row["status"] == "PENDING":
                        entry_price = float(window.iloc[0]["open"])
                        if row["timeframe"] == "4h":
                            entry_price *= 1.0002
                        quantity = float(row["notional"]) / entry_price
                        tp_price = entry_price * (1 + float(row["tp_pct"]) / 100)
                        sl_price = entry_price * (1 - float(row["sl_pct"]) / 100)
                        conn.execute(
                            """UPDATE positions SET status='OPEN',entry_price=?,quantity=?,
                            tp_price=?,sl_price=?,updated_at=datetime('now') WHERE position_id=?""",
                            (entry_price, quantity, tp_price, sl_price, row["position_id"]),
                        )
                    else:
                        entry_price = float(row["entry_price"])
                        quantity = float(row["quantity"])
                        tp_price = float(row["tp_price"])
                        sl_price = float(row["sl_price"])

                    base_exit = first_exit(window, sl_price, tp_price, int(row["deadline"]))
                    if base_exit is None:
                        base_exit_open = None
                        base_exit_time = end_ms
                        base_exit_price = None
                        base_reason = None
                    else:
                        base_exit_open, base_exit_time, base_exit_price, base_reason = base_exit
                        if row["timeframe"] == "4h":
                            base_exit_price *= 0.9998
                        gross = quantity * (base_exit_price - entry_price)
                        fee_rate = 0.0005 if row["timeframe"] == "4h" else 0.001
                        fees = (float(row["notional"]) + quantity * base_exit_price) * fee_rate
                        net = gross - fees
                        conn.execute(
                            """UPDATE positions SET status='CLOSED',exit_time=?,exit_price=?,
                            exit_reason=?,gross_pnl=?,fees=?,funding=0,net_pnl=?,updated_at=datetime('now')
                            WHERE position_id=?""",
                            (base_exit_time, base_exit_price, base_reason, gross, fees, net, row["position_id"]),
                        )

                    account_rows = list(conn.execute(
                        "SELECT * FROM execution_account_positions WHERE position_id=?",
                        (row["position_id"],),
                    ))
                    for account_row_original in account_rows:
                        account_row = dict(account_row_original)
                        config = EXECUTION_ACCOUNTS[account_row["account_id"]]
                        exposure = float(account_row["exposure_usdt"])
                        account_quantity = exposure / entry_price
                        liquidation = None
                        if config["market"] == "FUTURES":
                            liquidation = entry_price * (
                                1 - 1 / int(account_row["leverage"])
                                + settings.leverage_paper_maintenance_rate
                            )
                        entry_fee = exposure * float(config["fee_rate"])
                        conn.execute(
                            """UPDATE execution_account_positions SET status='OPEN',entry_price=?,
                            quantity=?,tp_price=?,sl_price=?,liquidation_price=?,entry_fee=?,updated_at=datetime('now')
                            WHERE account_position_id=?""",
                            (entry_price, account_quantity, tp_price, sl_price, liquidation,
                             entry_fee, account_row["account_position_id"]),
                        )
                        exit_time = base_exit_time
                        exit_price = base_exit_price
                        reason = base_reason
                        if liquidation is not None:
                            liq_hits = window[window["low"] <= liquidation]
                            if not liq_hits.empty:
                                liq_open = liq_hits.index[0]
                                if base_exit_open is None or liq_open <= base_exit_open:
                                    candle = liq_hits.iloc[0]
                                    exit_time = int(candle["close_time"])
                                    exit_price = liquidation
                                    reason = "liquidation"
                        if reason is None:
                            continue
                        gross = account_quantity * (exit_price - entry_price)
                        exit_fee = account_quantity * exit_price * float(config["fee_rate"])
                        funding = 0.0
                        if config["market"] == "FUTURES":
                            funding = funding_cost(rates, entry_time, exit_time, exposure)
                        net = gross - entry_fee - exit_fee - funding
                        conn.execute(
                            """UPDATE execution_account_positions SET status='CLOSED',exit_time=?,
                            exit_price=?,exit_reason=?,gross_pnl=?,exit_fee=?,funding=?,net_pnl=?,
                            return_pct=?,funding_error=?,updated_at=datetime('now')
                            WHERE account_position_id=?""",
                            (exit_time, exit_price, reason, gross, exit_fee, funding, net,
                             100 * net / float(account_row["margin_usdt"]), funding_error,
                             account_row["account_position_id"]),
                        )
                    completed += 1
                conn.commit()
                print(
                    f"settled {index}/{len(by_symbol)} {symbol} positions={len(symbol_rows)} "
                    f"candles={len(candles)}", flush=True
                )
            recompute(conn)
            conn.commit()
            counts = {row[0]: row[1] for row in conn.execute(
                "SELECT status,COUNT(*) FROM positions GROUP BY status"
            )}
        finally:
            await client.close()
    result = {
        "end": args.end,
        "positions_considered": len(positions),
        "positions_processed": completed,
        "symbols": len(by_symbol),
        "deduplicated_candle_rows": candle_rows,
        "unresolved_position_ids": sorted(set(unresolved)),
        "position_counts": counts,
    }
    Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        raise SystemExit("Refusing mutation without --apply")
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
