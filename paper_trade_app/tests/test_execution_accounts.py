from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace


APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from db import Database, utc_now
from engine import PaperTradeEngine
from execution_accounts import EXECUTION_ACCOUNTS


def test_four_independent_accounts_open_and_close(tmp_path):
    db = Database(tmp_path / "accounts.db")
    now = utc_now()
    db.execute(
        """INSERT INTO scenarios(scenario_id,timeframe,scenario,strategy,target,filter_name,
        threshold,horizon,tp_pct,sl_pct,vol_low,vol_high,enabled,blocked_reason,
        initial_cash,cash,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (1, "5m", "test", "test", "test", "threshold", .7, 5, 10, 5,
         0, 1, 1, None, 1000, 1000, now),
    )
    position_id = db.execute(
        """INSERT INTO positions(scenario_id,symbol,timeframe,target,signal_time,entry_time,
        deadline,status,notional,entry_price,quantity,tp_price,sl_price,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (1, "BTCUSDT", "5m", "test", 0, 1000, 999999, "OPEN", 10000,
         100, 100, 110, 95, now, now),
    )
    db.executemany(
        """INSERT INTO execution_account_positions(position_id,account_id,market_type,
        leverage,scenario_id,symbol,timeframe,status,signal_time,entry_time,deadline,
        margin_usdt,exposure_usdt,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ((position_id, key, cfg["market"], cfg["leverage"], 1, "BTCUSDT", "5m",
          "PENDING", 0, 1000, 999999, 10, 10 * cfg["leverage"], now, now)
         for key, cfg in EXECUTION_ACCOUNTS.items()),
    )
    engine = PaperTradeEngine.__new__(PaperTradeEngine)
    engine.db = db
    engine.settings = SimpleNamespace(leverage_paper_maintenance_rate=.005)
    asyncio.run(engine._open_execution_accounts({
        "position_id": position_id, "entry_price": 100, "tp_price": 110, "sl_price": 95,
    }))
    opened = db.query(
        "SELECT * FROM execution_account_positions WHERE position_id=? ORDER BY leverage",
        (position_id,),
    )
    assert len(opened) == 4
    assert [row["margin_usdt"] for row in opened] == [10, 10, 10, 10]
    assert [row["exposure_usdt"] for row in opened] == [10, 20, 50, 100]
    assert opened[-1]["liquidation_price"] == 90.5
    assert opened[0]["entry_fee"] == .01
    assert opened[-1]["entry_fee"] == .05

    async def no_funding(*_args):
        return 0.0, None

    engine._funding_cost = no_funding
    asyncio.run(engine._close_execution_accounts(
        {"position_id": position_id}, 2000, 105, "take"
    ))
    closed = db.query(
        "SELECT * FROM execution_account_positions WHERE position_id=? ORDER BY leverage",
        (position_id,),
    )
    assert all(row["status"] == "CLOSED" for row in closed)
    assert round(closed[0]["net_pnl"], 6) == round(.5 - .01 - .0105, 6)
    assert round(closed[-1]["net_pnl"], 6) == round(5 - .05 - .0525, 6)
