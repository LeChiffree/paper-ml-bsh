from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace


APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from db import Database, utc_now
from engine import PaperTradeEngine
from leverage_config import LEVERAGE_BY_SCENARIO


def test_leverage_scenario_contract():
    assert len(LEVERAGE_BY_SCENARIO) == 22
    assert sum(value == 10 for value in LEVERAGE_BY_SCENARIO.values()) == 11
    assert sum(value == 5 for value in LEVERAGE_BY_SCENARIO.values()) == 8
    assert sum(value == 2 for value in LEVERAGE_BY_SCENARIO.values()) == 3


def test_leverage_fee_funding_and_return_math(tmp_path):
    db = Database(tmp_path / "paper.db")
    now = utc_now()
    db.execute(
        """INSERT INTO scenarios(scenario_id,timeframe,scenario,strategy,target,filter_name,
        threshold,horizon,tp_pct,sl_pct,vol_low,vol_high,enabled,blocked_reason,
        initial_cash,cash,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (38, "4h", "test", "test", "test", "threshold", 0.7, 10, 10, 5,
         0, 1, 1, None, 1000, 1000, now),
    )
    position_id = db.execute(
        """INSERT INTO positions(scenario_id,symbol,timeframe,target,signal_time,entry_time,
        deadline,status,notional,entry_price,quantity,tp_price,sl_price,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (38, "BTCUSDT", "4h", "test", 0, 1000, 999999, "OPEN", 10000,
         100, 100, 110, 95, now, now),
    )
    db.execute(
        """INSERT INTO leverage_positions(position_id,scenario_id,symbol,timeframe,leverage,
        margin_usdt,exposure_usdt,status,signal_time,entry_time,deadline,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (position_id, 38, "BTCUSDT", "4h", 10, 10, 100, "PENDING", 0, 1000, 999999, now, now),
    )

    class Client:
        async def funding_rates(self, *_args, **_kwargs):
            return [{"fundingTime": 1500, "fundingRate": 0.001}]

    engine = PaperTradeEngine.__new__(PaperTradeEngine)
    engine.db, engine.client = db, Client()
    engine._funding_cache = {}
    engine.settings = SimpleNamespace(
        leverage_paper_fee_rate=0.0005,
        leverage_paper_maintenance_rate=0.005,
    )
    position = db.query("SELECT * FROM positions WHERE position_id=?", (position_id,))[0]
    engine._open_leverage_paper(position)
    opened = db.query("SELECT * FROM leverage_positions WHERE position_id=?", (position_id,))[0]
    assert opened["quantity"] == 1
    assert opened["entry_fee"] == 0.05
    assert round(opened["liquidation_price"], 6) == 90.5

    asyncio.run(engine._close_leverage_paper(position, 2000, 105, "take"))
    closed = db.query("SELECT * FROM leverage_positions WHERE position_id=?", (position_id,))[0]
    assert closed["gross_pnl"] == 5
    assert closed["exit_fee"] == 0.0525
    assert closed["funding"] == 0.1
    assert round(closed["net_pnl"], 6) == 4.7975
    assert round(closed["return_pct"], 6) == 47.975
