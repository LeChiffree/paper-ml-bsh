from __future__ import annotations

import json
import logging
import secrets
import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import Settings
from db import Database, utc_now
from engine import PaperTradeEngine
from leverage_config import LEVERAGE_BY_SCENARIO
from instance_lock import EngineInstanceLock
from execution_accounts import EXECUTION_ACCOUNTS


settings = Settings()
settings.log_path.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[logging.FileHandler(settings.log_path, encoding="utf-8"), logging.StreamHandler()],
)
db = Database(settings.db_path)
engine: PaperTradeEngine | None = None
engine_lock = EngineInstanceLock(settings.db_path.with_suffix(".engine.lock"))
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    global engine
    engine_lock.acquire()
    try:
        engine = PaperTradeEngine(settings, db)
        await engine.start()
        try:
            yield
        finally:
            await engine.stop()
    finally:
        engine = None
        engine_lock.release()


app = FastAPI(title="Model Arena Paper Trade", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    legacy_paths = {
        "/arena",
        "/spot-demo",
        "/leverage-x",
        "/api/status",
        "/api/universe",
        "/api/summary",
        "/api/scenarios",
        "/api/positions",
        "/api/events",
        "/api/trades",
    }
    if request.url.path in legacy_paths or request.url.path.startswith(
        ("/api/spot-demo", "/api/leverage-paper")
    ):
        return JSONResponse(
            {"detail": "Eski sistem iptal edildi; yalnızca MotorLab aktiftir."},
            status_code=410,
        )
    if request.url.path == "/healthz" or not settings.dashboard_auth_enabled:
        return await call_next(request)
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Basic "):
        return JSONResponse(
            {"detail": "Authentication required"},
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Paper Trade"'},
        )
    import base64

    try:
        user, password = base64.b64decode(auth[6:]).decode("utf-8").split(":", 1)
    except Exception:
        user, password = "", ""
    valid = secrets.compare_digest(user, settings.dashboard_user) and secrets.compare_digest(
        password, settings.dashboard_password
    )
    if not valid:
        return JSONResponse(
            {"detail": "Invalid credentials"},
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Paper Trade"'},
        )
    response = await call_next(request)
    if request.url.path == "/spot-demo" or request.url.path.startswith("/static/spot-demo"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@app.get("/healthz")
def health():
    return {"ok": True, "state": "initializing" if engine is None else engine.state}


@app.get("/api/status")
def status():
    if engine is None:
        return {"state": "initializing"}
    return engine.status()


@app.get("/api/universe")
def universe():
    if engine is None:
        return {"count": 0, "symbols": []}
    return {"count": len(engine.registry.symbols), "symbols": engine.registry.symbols}


@app.get("/api/summary")
def summary():
    totals = db.query(
        """SELECT COUNT(*) scenarios,
        SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END) enabled,
        SUM(initial_cash) initial_cash, SUM(cash) cash, SUM(realized_pnl) realized_pnl,
        SUM(trade_count) trades, SUM(win_count) wins, SUM(loss_count) losses,
        SUM(time_count) timed, SUM(skipped_capital) skipped_capital
        FROM scenarios"""
    )[0]
    positions = db.query(
        """SELECT status,COUNT(*) count,COALESCE(SUM(notional),0) notional
        FROM positions GROUP BY status"""
    )
    by_tf = db.query(
        """SELECT timeframe,COUNT(*) scenarios,
        SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END) enabled,
        SUM(realized_pnl) realized_pnl,SUM(trade_count) trades,SUM(win_count) wins
        FROM scenarios GROUP BY timeframe ORDER BY CASE timeframe WHEN '5m' THEN 1 WHEN '15m' THEN 2 WHEN '1h' THEN 3 ELSE 4 END"""
    )
    signals_24h = db.scalar(
        "SELECT COUNT(*) FROM signals WHERE created_at >= datetime('now','-1 day')"
    ) or 0
    return {"totals": totals, "positions": positions, "timeframes": by_tf, "signals_24h": signals_24h}


@app.get("/api/scenarios")
def scenarios(
    timeframe: str | None = None,
    enabled: int | None = None,
    search: str | None = None,
    limit: int = Query(300, ge=1, le=500),
):
    where, params = [], []
    if timeframe:
        where.append("timeframe=?")
        params.append(timeframe)
    if enabled is not None:
        where.append("enabled=?")
        params.append(enabled)
    if search:
        where.append("(scenario LIKE ? OR strategy LIKE ? OR target LIKE ?)")
        params.extend([f"%{search}%"] * 3)
    clause = " WHERE " + " AND ".join(where) if where else ""
    params.append(limit)
    return db.query(
        f"""SELECT scenario_id,timeframe,scenario,strategy,target,filter_name,threshold,horizon,
        tp_pct,sl_pct,enabled,blocked_reason,initial_cash,cash,realized_pnl,trade_count,
        win_count,loss_count,time_count,skipped_capital,skipped_same_symbol,updated_at,
        CASE WHEN trade_count>0 THEN 100.0*win_count/trade_count ELSE NULL END win_rate,
        100.0*realized_pnl/initial_cash return_pct
        FROM scenarios{clause} ORDER BY realized_pnl DESC LIMIT ?""",
        params,
    )


@app.get("/api/positions")
def positions(status: str | None = None, limit: int = Query(100, ge=1, le=500)):
    if status and status.upper() == "ACTIVE":
        return db.query(
            """SELECT p.*,s.scenario,s.strategy FROM positions p JOIN scenarios s USING(scenario_id)
            WHERE p.status IN ('PENDING','OPEN') ORDER BY p.updated_at DESC LIMIT ?""",
            (limit,),
        )
    if status:
        return db.query(
            """SELECT p.*,s.scenario,s.strategy FROM positions p JOIN scenarios s USING(scenario_id)
            WHERE p.status=? ORDER BY p.updated_at DESC LIMIT ?""",
            (status.upper(), limit),
        )
    return db.query(
        """SELECT p.*,s.scenario,s.strategy FROM positions p JOIN scenarios s USING(scenario_id)
        ORDER BY p.updated_at DESC LIMIT ?""",
        (limit,),
    )


@app.get("/api/events")
def events(limit: int = Query(80, ge=1, le=300)):
    return db.query("SELECT * FROM events ORDER BY event_id DESC LIMIT ?", (limit,))


@app.get("/api/trades")
def trades(limit: int = Query(100, ge=1, le=500)):
    return db.query(
        """SELECT p.*,s.scenario,s.strategy FROM positions p JOIN scenarios s USING(scenario_id)
        WHERE p.status='CLOSED' ORDER BY p.exit_time DESC LIMIT ?""",
        (limit,),
    )


@app.get("/api/spot-demo")
async def spot_demo_summary():
    if engine is None:
        return {"connected": False, "selected_scenarios": 0, "orders": []}
    counts = db.query("SELECT status,COUNT(*) count FROM spot_demo_orders GROUP BY status")
    selected = db.query(
        f"""SELECT scenario_id,timeframe,scenario,strategy,target,filter_name,threshold,enabled,blocked_reason
        FROM scenarios WHERE scenario_id IN ({','.join('?' for _ in engine.spot_scenario_ids)})
        ORDER BY CASE timeframe WHEN '5m' THEN 1 WHEN '15m' THEN 2 WHEN '1h' THEN 3 ELSE 4 END,scenario_id""",
        sorted(engine.spot_scenario_ids),
    ) if engine.spot_scenario_ids else []
    availability = []
    for symbol in engine.registry.symbols:
        spot_symbol = engine.spot_client.spot_symbol(symbol)
        futures_symbol = symbol if not spot_symbol and engine.futures_client.supports(symbol) else None
        availability.append({"paper_symbol": symbol, "spot_symbol": spot_symbol,
                             "futures_symbol": futures_symbol,
                             "route": "SPOT" if spot_symbol else ("FUTURES_1X" if futures_symbol else None)})
    open_rows = db.query(
        """SELECT d.*,p.timeframe,p.entry_price paper_entry_price,s.scenario FROM spot_demo_orders d
        JOIN positions p USING(position_id) JOIN scenarios s USING(scenario_id)
        WHERE d.status='OPEN' ORDER BY d.updated_at DESC"""
    )
    spot_result, futures_result = await asyncio.gather(
        engine.spot_client.ticker_prices(), engine.futures_client.ticker_prices(), return_exceptions=True
    )
    spot_prices = {} if isinstance(spot_result, Exception) else spot_result
    futures_prices = {} if isinstance(futures_result, Exception) else futures_result
    active_positions = []
    for row in open_rows:
        prices = futures_prices if row["venue"] == "FUTURES_1X" else spot_prices
        current_price = prices.get(row["spot_symbol"])
        current_price_source = "binance_ticker"
        if current_price is None:
            current_price = engine.last_market_prices.get(row["paper_symbol"])
            current_price_source = "last_closed_1m" if current_price is not None else "unavailable"
        quantity = float(row["base_quantity"] or 0)
        recorded_cost = float(row["buy_quote"] or 0)
        if recorded_cost > 0 and quantity > 0:
            entry_price = recorded_cost / quantity
            entry_price_source = "binance_fill"
        else:
            entry_price = float(row["paper_entry_price"] or 0)
            entry_price_source = "paper_fallback"
        cost = quantity * entry_price
        market_value = None if current_price is None else quantity * current_price
        unrealized = None if market_value is None else market_value - cost
        active_positions.append({**row, "entry_price": entry_price,
                                 "entry_price_source": entry_price_source,
                                 "effective_cost": cost,
                                 "current_price": current_price,
                                 "current_price_source": current_price_source,
                                 "market_value": market_value,
                                 "unrealized_pnl": unrealized,
                                 "unrealized_pct": None if unrealized is None or not cost else 100 * unrealized / cost})
    closed_rows = db.query(
        """SELECT d.*,p.entry_price paper_entry_price,p.exit_price paper_exit_price
        FROM spot_demo_orders d JOIN positions p USING(position_id)
        WHERE d.status='CLOSED'"""
    )

    def effective_quotes(row: dict[str, Any]) -> tuple[float, float]:
        quantity = float(row.get("base_quantity") or 0)
        buy = float(row.get("buy_quote") or 0)
        sell = float(row.get("sell_quote") or 0)
        if row.get("venue") == "FUTURES_1X":
            if buy <= 0:
                buy = quantity * float(row.get("paper_entry_price") or 0)
            if sell <= 0 and row.get("status") == "CLOSED":
                sell = quantity * float(row.get("paper_exit_price") or 0)
        return buy, sell

    closed_pnls = [sell - buy for row in closed_rows for buy, sell in [effective_quotes(row)]]
    fee_rows = db.query(
        """SELECT d.*,p.entry_price paper_entry_price,p.exit_price paper_exit_price
        FROM spot_demo_orders d JOIN positions p USING(position_id)
        WHERE d.status IN ('OPEN','CLOSED','CLOSE_ERROR')"""
    )
    estimated_fees = sum(
        sum(effective_quotes(row))
        * (0.0005 if row["venue"] == "FUTURES_1X" else 0.001)
        for row in fee_rows
    )
    unrealized_total = sum(row["unrealized_pnl"] for row in active_positions if row["unrealized_pnl"] is not None)
    profitable_open = [row for row in active_positions if (row["unrealized_pnl"] or 0) > 0]
    losing_open = [row for row in active_positions if (row["unrealized_pnl"] or 0) < 0]
    flat_open = [row for row in active_positions if row["unrealized_pnl"] == 0]
    closed_pnl = sum(closed_pnls)
    performance = {"open_count": len(active_positions),
                   "open_notional": sum(float(row["effective_cost"] or 0) for row in active_positions),
                   "unrealized_pnl": unrealized_total,
                   "open_profit_amount": sum(row["unrealized_pnl"] for row in profitable_open),
                   "open_loss_amount": sum(row["unrealized_pnl"] for row in losing_open),
                   "open_profit_count": len(profitable_open),
                   "open_loss_count": len(losing_open),
                   "open_flat_count": len(flat_open),
                   "priced_open_count": sum(row["current_price"] is not None for row in active_positions),
                   "spot_open_count": sum(row["venue"] == "SPOT" for row in open_rows),
                   "futures_open_count": sum(row["venue"] == "FUTURES_1X" for row in open_rows),
                   "closed_count": len(closed_rows), "closed_pnl": closed_pnl,
                   "closed_wins": sum(value > 0 for value in closed_pnls),
                   "estimated_fees": estimated_fees,
                   "total_pnl_after_estimated_fees": unrealized_total + closed_pnl - estimated_fees}

    def breakdown(field: str) -> list[dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for row in active_positions:
            key = str(row.get(field) or "Bilinmiyor")
            item = groups.setdefault(key, {
                "name": key, "positions": 0, "notional": 0.0, "pnl": 0.0,
                "profit_count": 0, "loss_count": 0, "priced_count": 0,
            })
            pnl = row.get("unrealized_pnl")
            item["positions"] += 1
            item["notional"] += float(row.get("effective_cost") or 0)
            if pnl is not None:
                item["priced_count"] += 1
                item["pnl"] += float(pnl)
                item["profit_count"] += int(pnl > 0)
                item["loss_count"] += int(pnl < 0)
        return list(groups.values())

    scenario_groups: dict[str, dict[str, Any]] = {}
    for row in active_positions:
        key = str(row["scenario_id"])
        item = scenario_groups.setdefault(key, {
            "scenario_id": row["scenario_id"], "scenario": row["scenario"],
            "timeframe": row["timeframe"], "positions": 0, "notional": 0.0,
            "pnl": 0.0, "profit_count": 0, "loss_count": 0,
        })
        pnl = row.get("unrealized_pnl")
        item["positions"] += 1
        item["notional"] += float(row.get("effective_cost") or 0)
        if pnl is not None:
            item["pnl"] += float(pnl)
            item["profit_count"] += int(pnl > 0)
            item["loss_count"] += int(pnl < 0)

    symbol_groups = breakdown("paper_symbol")
    priced_positions = [row for row in active_positions if row.get("unrealized_pnl") is not None]
    overview = {
        "venue": sorted(breakdown("venue"), key=lambda row: row["name"]),
        "timeframe": sorted(
            breakdown("timeframe"),
            key=lambda row: {"5m": 0, "15m": 1, "1h": 2, "4h": 3}.get(row["name"], 9),
        ),
        "scenarios": sorted(
            scenario_groups.values(), key=lambda row: abs(row["pnl"]), reverse=True
        )[:15],
        "symbols": sorted(symbol_groups, key=lambda row: row["notional"], reverse=True)[:12],
        "top_winners": sorted(
            priced_positions, key=lambda row: row["unrealized_pnl"], reverse=True
        )[:8],
        "top_losers": sorted(
            priced_positions, key=lambda row: row["unrealized_pnl"]
        )[:8],
        "fill_quality": {
            "binance_fill": sum(row["entry_price_source"] == "binance_fill" for row in active_positions),
            "paper_fallback": sum(row["entry_price_source"] == "paper_fallback" for row in active_positions),
            "priced": len(priced_positions),
            "unpriced": len(active_positions) - len(priced_positions),
            "ticker_price": sum(
                row["current_price_source"] == "binance_ticker" for row in active_positions
            ),
            "last_candle_price": sum(
                row["current_price_source"] == "last_closed_1m" for row in active_positions
            ),
        },
    }
    raw_data_alerts = db.query(
        """SELECT level,message,payload,created_at FROM events
        WHERE category='data_integrity' ORDER BY event_id DESC LIMIT 100"""
    )
    silent_pairs = settings.silent_integrity_pairs()
    data_alerts = []
    for alert in raw_data_alerts:
        try:
            payload = json.loads(alert.get("payload") or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict) and (
            payload.get("timeframe"), payload.get("symbol")
        ) in silent_pairs:
            continue
        data_alerts.append(alert)
        if len(data_alerts) == 20:
            break
    return {"configured": engine.spot_client.configured, "connected": engine.spot_demo_connected,
            "error": engine.spot_demo_error, "futures_connected": engine.futures_demo_connected,
            "futures_error": engine.futures_demo_error, "quote_usdt": settings.spot_demo_quote_usdt,
            "selected_scenarios": selected, "order_counts": counts, "availability": availability,
            "active_positions": active_positions, "performance": performance, "overview": overview,
            "data_alerts": data_alerts, "prices_as_of": utc_now(),
            "price_error": "; ".join(str(x) for x in (spot_result, futures_result) if isinstance(x, Exception)) or None}


@app.get("/api/spot-demo/orders")
def spot_demo_orders(limit: int = Query(200, ge=1, le=500)):
    return db.query(
        """SELECT d.*,p.timeframe,p.entry_price,p.exit_price,p.exit_reason,s.scenario
        FROM spot_demo_orders d JOIN positions p USING(position_id) JOIN scenarios s USING(scenario_id)
        ORDER BY d.mirror_id DESC LIMIT ?""", (limit,))


@app.get("/api/leverage-paper")
async def leverage_paper_summary():
    if engine is None:
        return {"state": "initializing"}
    scenario_ids = sorted(LEVERAGE_BY_SCENARIO)
    placeholders = ",".join("?" for _ in scenario_ids)
    scenario_rows = db.query(
        f"""SELECT scenario_id,timeframe,scenario,tp_pct,sl_pct,horizon,enabled
        FROM scenarios WHERE scenario_id IN ({placeholders}) ORDER BY scenario_id""",
        scenario_ids,
    )
    aggregates = {
        row["scenario_id"]: row for row in db.query(
            f"""SELECT scenario_id,COUNT(*) positions,
            SUM(CASE WHEN status IN ('PENDING','OPEN') THEN 1 ELSE 0 END) open_positions,
            SUM(CASE WHEN status='CLOSED' THEN 1 ELSE 0 END) closed_positions,
            COALESCE(SUM(CASE WHEN status='CLOSED' THEN net_pnl ELSE 0 END),0) net_pnl,
            COALESCE(AVG(CASE WHEN status='CLOSED' THEN return_pct END),0) avg_return_pct,
            SUM(CASE WHEN status='CLOSED' AND net_pnl>0 THEN 1 ELSE 0 END) wins
            FROM leverage_positions WHERE scenario_id IN ({placeholders}) GROUP BY scenario_id""",
            scenario_ids,
        )
    }
    scenarios_payload = []
    for row in scenario_rows:
        stats = aggregates.get(row["scenario_id"], {})
        scenarios_payload.append({
            **row, "leverage": LEVERAGE_BY_SCENARIO[row["scenario_id"]],
            "positions": stats.get("positions", 0), "open_positions": stats.get("open_positions", 0),
            "closed_positions": stats.get("closed_positions", 0), "net_pnl": stats.get("net_pnl", 0),
            "avg_return_pct": stats.get("avg_return_pct", 0), "wins": stats.get("wins", 0),
        })
    open_rows = db.query(
        """SELECT l.*,s.scenario FROM leverage_positions l JOIN scenarios s USING(scenario_id)
        WHERE l.status IN ('PENDING','OPEN') ORDER BY l.updated_at DESC"""
    )
    try:
        prices = await engine.futures_client.ticker_prices()
        price_error = None
    except Exception as exc:
        prices, price_error = {}, f"{type(exc).__name__}: {exc}"
    active = []
    now_ms = int(time.time() * 1000)
    for row in open_rows:
        price = prices.get(row["symbol"])
        gross = estimated_exit_fee = net = return_pct = None
        accrued_funding = float(row["funding"] or 0)
        funding_error = row.get("funding_error")
        if row["status"] == "OPEN" and price is not None and row["quantity"] is not None:
            accrued_funding, live_funding_error = await engine._funding_cost(
                row["symbol"], int(row["entry_time"]), now_ms, float(row["exposure_usdt"])
            )
            funding_error = live_funding_error or funding_error
            gross = float(row["quantity"]) * (price - float(row["entry_price"]))
            estimated_exit_fee = float(row["quantity"]) * price * settings.leverage_paper_fee_rate
            net = gross - float(row["entry_fee"] or 0) - estimated_exit_fee - accrued_funding
            return_pct = 100 * net / float(row["margin_usdt"])
        active.append({**row, "current_price": price, "unrealized_gross": gross,
                       "estimated_exit_fee": estimated_exit_fee,
                       "funding": accrued_funding, "funding_error": funding_error,
                       "unrealized_net": net, "unrealized_return_pct": return_pct})
    totals = db.query(
        """SELECT COUNT(*) positions,
        SUM(CASE WHEN status IN ('PENDING','OPEN') THEN 1 ELSE 0 END) open_positions,
        SUM(CASE WHEN status='CLOSED' THEN 1 ELSE 0 END) closed_positions,
        COALESCE(SUM(CASE WHEN status IN ('PENDING','OPEN') THEN margin_usdt ELSE 0 END),0) open_margin,
        COALESCE(SUM(CASE WHEN status IN ('PENDING','OPEN') THEN exposure_usdt ELSE 0 END),0) open_exposure,
        COALESCE(SUM(entry_fee+exit_fee),0) total_fees,
        COALESCE(SUM(funding),0) total_funding,
        COALESCE(SUM(CASE WHEN status='CLOSED' THEN gross_pnl ELSE 0 END),0) realized_gross,
        COALESCE(SUM(CASE WHEN status='CLOSED' THEN net_pnl ELSE 0 END),0) realized_net,
        SUM(CASE WHEN status='CLOSED' AND net_pnl>0 THEN 1 ELSE 0 END) wins
        FROM leverage_positions"""
    )[0]
    totals["unrealized_net"] = sum(float(row["unrealized_net"] or 0) for row in active)
    totals["open_funding"] = sum(float(row["funding"] or 0) for row in active)
    totals["funding_including_open"] = float(totals["total_funding"] or 0) + totals["open_funding"]
    totals["total_net"] = float(totals["realized_net"] or 0) + totals["unrealized_net"]
    return {"paper_only": True, "margin_per_signal": settings.leverage_paper_margin_usdt,
            "fee_rate": settings.leverage_paper_fee_rate, "scenarios": scenarios_payload,
            "active_positions": active, "totals": totals, "price_error": price_error,
            "prices_as_of": utc_now()}


@app.get("/api/leverage-paper/positions")
def leverage_paper_positions(limit: int = Query(300, ge=1, le=1000)):
    return db.query(
        """SELECT l.*,s.scenario FROM leverage_positions l JOIN scenarios s USING(scenario_id)
        ORDER BY l.leverage_position_id DESC LIMIT ?""", (limit,)
    )


@app.get("/api/motors")
async def motors_summary():
    if engine is None:
        return {"state": "stopped", "accounts": [], "active_positions": []}
    rows = db.query(
        """SELECT e.*,s.scenario FROM execution_account_positions e
        JOIN scenarios s USING(scenario_id) ORDER BY e.account_position_id DESC"""
    )
    demo_rows = db.query("SELECT * FROM futures_demo_mirrors ORDER BY mirror_id DESC")
    demo_by_account_position = {
        row["account_position_id"]: row for row in demo_rows
    }
    try:
        prices = await engine.futures_client.ticker_prices()
        price_error = None
    except Exception as exc:
        prices, price_error = {}, f"{type(exc).__name__}: {exc}"
    active = []
    now_ms = int(time.time() * 1000)
    for row in rows:
        if row["status"] not in {"PENDING", "OPEN"}:
            continue
        current_price = prices.get(row["symbol"])
        price_source = "binance_ticker"
        if current_price is None:
            current_price = engine.last_market_prices.get(row["symbol"])
            price_source = "last_closed_1m" if current_price is not None else "unavailable"
        gross = estimated_exit_fee = net = return_pct = None
        funding = float(row["funding"] or 0)
        funding_error = row.get("funding_error")
        if row["status"] == "OPEN" and current_price is not None and row["quantity"] is not None:
            if row["market_type"] == "FUTURES":
                funding, live_error = await engine._funding_cost(
                    row["symbol"], int(row["entry_time"]), now_ms,
                    float(row["exposure_usdt"]),
                )
                funding_error = live_error or funding_error
            gross = float(row["quantity"]) * (
                float(current_price) - float(row["entry_price"])
            )
            fee_rate = float(EXECUTION_ACCOUNTS[row["account_id"]]["fee_rate"])
            estimated_exit_fee = float(row["quantity"]) * float(current_price) * fee_rate
            net = gross - float(row["entry_fee"] or 0) - estimated_exit_fee - funding
            return_pct = 100 * net / float(row["margin_usdt"])
        active.append({
            **row, "current_price": current_price, "price_source": price_source,
            "unrealized_gross": gross, "estimated_exit_fee": estimated_exit_fee,
            "live_funding": funding, "funding_error": funding_error,
            "unrealized_net": net, "unrealized_return_pct": return_pct,
            "demo_order": demo_by_account_position.get(row["account_position_id"]),
        })

    accounts = []
    equity_history = {account_id: [] for account_id in EXECUTION_ACCOUNTS}
    running_equity = {
        account_id: settings.execution_account_initial_equity
        for account_id in EXECUTION_ACCOUNTS
    }
    closed_chronological = sorted(
        (row for row in rows if row["status"] == "CLOSED" and row["exit_time"] is not None),
        key=lambda row: (int(row["exit_time"]), int(row["account_position_id"])),
    )
    for row in closed_chronological:
        account_id = row["account_id"]
        running_equity[account_id] += float(row["net_pnl"] or 0)
        equity_history[account_id].append({
            "time": int(row["exit_time"]),
            "equity": running_equity[account_id],
        })
    for account_id, config in EXECUTION_ACCOUNTS.items():
        account_rows = [row for row in rows if row["account_id"] == account_id]
        open_rows = [row for row in active if row["account_id"] == account_id]
        closed_rows = [row for row in account_rows if row["status"] == "CLOSED"]
        realized_gross = sum(float(row["gross_pnl"] or 0) for row in closed_rows)
        realized_net = sum(float(row["net_pnl"] or 0) for row in closed_rows)
        unrealized_net = sum(float(row["unrealized_net"] or 0) for row in open_rows)
        open_margin = sum(float(row["margin_usdt"] or 0) for row in open_rows)
        open_exposure = sum(float(row["exposure_usdt"] or 0) for row in open_rows)
        closed_fees = sum(
            float(row["entry_fee"] or 0) + float(row["exit_fee"] or 0)
            for row in closed_rows
        )
        open_fees = sum(
            float(row["entry_fee"] or 0) + float(row["estimated_exit_fee"] or 0)
            for row in open_rows
        )
        total_funding = sum(float(row["funding"] or 0) for row in closed_rows) + sum(
            float(row["live_funding"] or 0) for row in open_rows
        )
        wins = sum(float(row["net_pnl"] or 0) > 0 for row in closed_rows)
        losses = sum(float(row["net_pnl"] or 0) < 0 for row in closed_rows)
        liquidations = sum(row["exit_reason"] == "liquidation" for row in closed_rows)
        initial_equity = settings.execution_account_initial_equity
        accounts.append({
            "account_id": account_id, **config,
            "initial_equity": initial_equity,
            "equity": initial_equity + realized_net + unrealized_net,
            "available_equity": initial_equity + realized_net - open_margin,
            "positions": len(account_rows), "active_signals": len(open_rows),
            "pending_signals": sum(row["status"] == "PENDING" for row in open_rows),
            "closed_signals": len(closed_rows), "wins": wins, "losses": losses,
            "liquidations": liquidations,
            "win_rate": None if not closed_rows else 100 * wins / len(closed_rows),
            "open_margin": open_margin, "open_exposure": open_exposure,
            "realized_gross": realized_gross, "realized_net": realized_net,
            "unrealized_net": unrealized_net,
            "total_net": realized_net + unrealized_net,
            "fees": closed_fees + open_fees, "funding": total_funding,
        })
    spot_strategy_pnl = []
    active_specs = sorted(
        (spec for spec in engine.scenarios if spec.scenario_id in engine.active_scenario_ids),
        key=lambda spec: spec.scenario_id,
    )
    for spec in active_specs:
        strategy_rows = [
            row for row in rows
            if row["account_id"] == "spot_1x"
            and row["scenario_id"] == spec.scenario_id
            and row["status"] == "CLOSED"
        ]
        spot_strategy_pnl.append({
            "scenario_id": spec.scenario_id, "scenario": spec.scenario,
            "timeframe": spec.timeframe, "closed_signals": len(strategy_rows),
            "wins": sum(float(row["net_pnl"] or 0) > 0 for row in strategy_rows),
            "net_pnl": sum(float(row["net_pnl"] or 0) for row in strategy_rows),
        })

    # Dashboard analytics use the Spot book as the canonical signal ledger.
    # The leveraged books are parallel outcomes of the same signal and must not
    # inflate open/closed counts.
    live_by_key = {
        (row["account_id"], row["position_id"]): row for row in active
    }

    def row_live_net(row):
        if row["status"] == "CLOSED":
            return float(row["net_pnl"] or 0)
        live = live_by_key.get((row["account_id"], row["position_id"]))
        return float((live or {}).get("unrealized_net") or 0)

    scenario_dashboard = []
    for spec in active_specs:
        spot_rows = [
            row for row in rows
            if row["account_id"] == "spot_1x" and row["scenario_id"] == spec.scenario_id
        ]
        account_returns = {}
        account_pnl = {}
        for account_id in EXECUTION_ACCOUNTS:
            account_rows = [
                row for row in rows
                if row["account_id"] == account_id and row["scenario_id"] == spec.scenario_id
            ]
            allocated_margin = sum(float(row["margin_usdt"] or 0) for row in account_rows)
            net_pnl = sum(row_live_net(row) for row in account_rows)
            account_pnl[account_id] = net_pnl
            account_returns[account_id] = (
                None if allocated_margin <= 0 else 100 * net_pnl / allocated_margin
            )
        scenario_dashboard.append({
            "scenario_id": spec.scenario_id,
            "scenario": spec.scenario,
            "timeframe": spec.timeframe,
            "open_signals": sum(row["status"] in {"PENDING", "OPEN"} for row in spot_rows),
            "closed_signals": sum(row["status"] == "CLOSED" for row in spot_rows),
            "account_returns": account_returns,
            "account_pnl": account_pnl,
        })

    spot_rows_all = [row for row in rows if row["account_id"] == "spot_1x"]
    coin_dashboard = []
    for symbol in sorted({row["symbol"] for row in spot_rows_all}):
        symbol_rows = [row for row in spot_rows_all if row["symbol"] == symbol]
        net_pnl = sum(row_live_net(row) for row in symbol_rows)
        margin = sum(float(row["margin_usdt"] or 0) for row in symbol_rows)
        coin_dashboard.append({
            "symbol": symbol,
            "signals": len(symbol_rows),
            "net_pnl": net_pnl,
            "return_pct": None if margin <= 0 else 100 * net_pnl / margin,
        })
    coin_dashboard.sort(key=lambda item: abs(item["net_pnl"]), reverse=True)

    timeframe_dashboard = []
    for timeframe in ("15m", "1h", "4h"):
        tf_rows = [row for row in spot_rows_all if row["timeframe"] == timeframe]
        margin = sum(float(row["margin_usdt"] or 0) for row in tf_rows)
        net_pnl = sum(row_live_net(row) for row in tf_rows)
        timeframe_dashboard.append({
            "timeframe": timeframe,
            "open_signals": sum(row["status"] in {"PENDING", "OPEN"} for row in tf_rows),
            "closed_signals": sum(row["status"] == "CLOSED" for row in tf_rows),
            "net_pnl": net_pnl,
            "return_pct": None if margin <= 0 else 100 * net_pnl / margin,
        })
    return {
        "state": engine.state, "paper_only": False,
        "demo_execution": {
            "enabled": settings.motorlab_demo_enabled,
            "execution_accounts": sorted(settings.motorlab_demo_accounts()),
            "margin_usdt": settings.motorlab_demo_margin_usdt,
            "futures_10x_notional_usdt": settings.motorlab_demo_margin_usdt * 10,
            "accounts": engine.demo_connections,
            "counts": {
                row["status"]: row["count"] for row in db.query(
                    "SELECT status,COUNT(*) count FROM futures_demo_mirrors GROUP BY status"
                )
            },
        },
        "margin_per_signal": settings.execution_account_margin_usdt,
        "active_scenario_ids": sorted(engine.active_scenario_ids),
        "equity_history": equity_history,
        "spot_strategy_pnl": spot_strategy_pnl,
        "scenario_dashboard": scenario_dashboard,
        "coin_dashboard": coin_dashboard,
        "timeframe_dashboard": timeframe_dashboard,
        "accounts": accounts, "active_positions": active[:1000],
        "recent_closed": [row for row in rows if row["status"] == "CLOSED"][:300],
        "price_error": price_error, "prices_as_of": utc_now(),
    }


@app.get("/api/motors/positions")
def motors_positions(
    account_id: str | None = None,
    status: str | None = None,
    limit: int = Query(500, ge=1, le=2000),
):
    where, params = [], []
    if account_id:
        if account_id not in EXECUTION_ACCOUNTS:
            raise HTTPException(400, "Invalid account")
        where.append("e.account_id=?")
        params.append(account_id)
    if status:
        where.append("e.status=?")
        params.append(status.upper())
    clause = " WHERE " + " AND ".join(where) if where else ""
    params.append(limit)
    return db.query(
        f"""SELECT e.*,s.scenario FROM execution_account_positions e
        JOIN scenarios s USING(scenario_id){clause}
        ORDER BY e.account_position_id DESC LIMIT ?""",
        params,
    )


@app.post("/api/control/cycle/{timeframe}")
async def manual_cycle(timeframe: str):
    if timeframe not in {"5m", "15m", "1h", "4h"}:
        raise HTTPException(400, "Invalid timeframe")
    if engine is None or engine.state not in {"running", "shadow-ready"}:
        raise HTTPException(409, "Engine is not ready")
    await engine.run_timeframe_cycle(timeframe)
    return {"ok": True, "timeframe": timeframe}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "motors-dashboard.html")


@app.get("/history")
def motors_history_page():
    return FileResponse(STATIC_DIR / "motors-history.html")


@app.get("/arena")
def arena_page():
    raise HTTPException(410, "Eski Arena iptal edildi; yalnızca MotorLab aktiftir.")


@app.get("/spot-demo")
def spot_demo_page():
    raise HTTPException(410, "Eski Spot/Futures Demo sistemi iptal edildi.")


@app.get("/leverage-x")
def leverage_paper_page():
    raise HTTPException(410, "Eski KaldıraçX sistemi iptal edildi.")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
