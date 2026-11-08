"""Standalone dashboard for the packaged 219-scenario backtest workbook.

Run from the repository root:
    uvicorn paper_trade_app.backtest_dashboard:app --host 0.0.0.0 --port 8766
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
WORKBOOK = (
    ROOT
    / "PAPER_TRADE_POSITIVE_MODELS_1M_EXECUTION"
    / "paper_trade_package"
    / "BACKTEST_POSITIVE_METHODS_1M_EXECUTION.xlsx"
)
ACTIVE_IDS = {5, 9, 10, 15, 19, 49, 56, 64, 79, 108, 111}

app = FastAPI(title="MotorLab Backtest Dashboard", version="1.0.0")


def _clean(value):
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


def _records(frame: pd.DataFrame) -> list[dict]:
    return [{key: _clean(value) for key, value in row.items()} for row in frame.to_dict("records")]


@lru_cache(maxsize=1)
def _data() -> tuple[pd.DataFrame, pd.DataFrame]:
    if not WORKBOOK.exists():
        raise FileNotFoundError(f"Backtest workbook not found: {WORKBOOK}")
    summary = pd.read_excel(WORKBOOK, sheet_name="Pozitif_Sonuclar")
    trades = pd.read_excel(WORKBOOK, sheet_name="Detayli_Islemler")
    summary["active"] = summary["scenario_id"].isin(ACTIVE_IDS)
    return summary, trades


@app.get("/healthz")
def healthz():
    try:
        summary, trades = _data()
        return {"ok": True, "scenarios": len(summary), "trades": len(trades)}
    except Exception as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/backtest/overview")
def overview():
    summary, trades = _data()
    by_timeframe = (
        summary.groupby("timeframe", as_index=False)
        .agg(
            scenarios=("scenario_id", "count"),
            trades=("trades", "sum"),
            net_pnl_tl=("net_pnl_tl", "sum"),
            average_return_pct=("return_pct", "mean"),
            average_drawdown_pct=("max_drawdown_pct", "mean"),
        )
        .sort_values("timeframe")
    )
    outcome_counts = trades["outcome"].value_counts(dropna=False).rename_axis("outcome").reset_index(name="count")
    return {
        "workbook": WORKBOOK.name,
        "scenario_count": int(len(summary)),
        "active_scenario_count": int(summary["active"].sum()),
        "active_scenario_ids": sorted(ACTIVE_IDS),
        "trade_count": int(len(trades)),
        "symbols": int(trades["symbol"].nunique()),
        "net_pnl_tl": float(summary["net_pnl_tl"].sum()),
        "average_return_pct": float(summary["return_pct"].mean()),
        "median_return_pct": float(summary["return_pct"].median()),
        "by_timeframe": _records(by_timeframe),
        "outcomes": _records(outcome_counts),
    }


@app.get("/api/backtest/scenarios")
def scenarios(
    timeframe: str | None = None,
    filter_name: str | None = Query(None, alias="filter"),
    active_only: bool = False,
):
    summary, _ = _data()
    view = summary
    if timeframe:
        view = view[view["timeframe"] == timeframe]
    if filter_name:
        view = view[view["filter"] == filter_name]
    if active_only:
        view = view[view["active"]]
    view = view.sort_values(["return_pct", "scenario_id"], ascending=[False, True])
    return {
        "count": int(len(view)),
        "timeframes": sorted(summary["timeframe"].dropna().unique().tolist()),
        "filters": sorted(summary["filter"].dropna().unique().tolist()),
        "items": _records(view),
    }


@app.get("/api/backtest/trades")
def trades(
    scenario_id: int | None = None,
    symbol: str | None = None,
    outcome: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
):
    _, detail = _data()
    view = detail
    if scenario_id is not None:
        view = view[view["scenario_id"] == scenario_id]
    if symbol:
        view = view[view["symbol"] == symbol.upper()]
    if outcome:
        view = view[view["outcome"] == outcome.upper()]
    view = view.sort_values("signal_time", ascending=False)
    return {"total": int(len(view)), "items": _records(view.iloc[offset : offset + limit])}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "backtest-dashboard.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

