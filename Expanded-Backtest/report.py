#!/usr/bin/env python3
"""Aggregate expanded normal/singular trades into the dashboard payload."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "Singular-Backtest"))

from data import INITIAL_TL, STAKE_TL
from run_singular import _max_drawdown, _round

INITIAL = INITIAL_TL


def metrics(g: pd.DataFrame, capital: float = INITIAL) -> dict:
    if g.empty:
        return {
            "trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "avg_ret": 0.0, "retAll": 0.0, "ret2026": 0.0,
            "winRatio": 0.0, "winHoldH": 0.0,
            "profitDays": 0, "lossDays": 0, "maxDD": 0.0,
            "tp": 0, "sl": 0, "time": 0, "net_pnl_tl": 0.0,
            "symbols": 0,
        }
    win = g["net_pnl_tl"] > 0
    hold_h = (g["exit_time"] - g["entry_time"]).dt.total_seconds() / 3600.0
    daily = g.groupby(g["exit_time"].dt.floor("D"))["net_pnl_tl"].sum()
    chrono = g.sort_values("exit_time", kind="mergesort")
    year2026 = g["exit_time"].dt.year == 2026
    wins = int(win.sum())
    losses = int((~win).sum())
    outcome = g["outcome"].astype(str)
    return {
        "trades": int(len(g)),
        "wins": wins,
        "losses": losses,
        "win_rate": wins / len(g),
        "avg_ret": float(g["net_return_pct"].mean()),
        "retAll": float(g["net_pnl_tl"].sum() / capital * 100.0),
        "ret2026": float(g.loc[year2026, "net_pnl_tl"].sum() / capital * 100.0),
        "winRatio": wins / losses if losses else float(wins),
        "winHoldH": float(hold_h[win].mean()) if win.any() else 0.0,
        "profitDays": int((daily > 0).sum()),
        "lossDays": int((daily < 0).sum()),
        "maxDD": _max_drawdown(chrono["net_pnl_tl"], capital),
        "tp": int((outcome == "TP").sum()),
        "sl": int((outcome == "SL").sum()),
        "time": int((outcome == "TIME").sum()),
        "net_pnl_tl": float(g["net_pnl_tl"].sum()),
        "symbols": int(g["symbol"].nunique()),
    }


def prefixed(g: pd.DataFrame, prefix: str, capital: float) -> dict:
    return {f"{prefix}{k}": v for k, v in metrics(g, capital).items()}


def simple(g: pd.DataFrame, capital: float) -> dict:
    if g.empty:
        return {"trades": 0, "retAll": 0.0, "win_rate": 0.0, "avg_ret": 0.0}
    return {
        "trades": int(len(g)),
        "retAll": float(g["net_pnl_tl"].sum() / capital * 100.0),
        "win_rate": float((g["net_pnl_tl"] > 0).mean()),
        "avg_ret": float(g["net_return_pct"].mean()),
    }


def aggregate(normal: pd.DataFrame, singular: pd.DataFrame, specs: list,
              meta: dict) -> dict:
    by_sid = {int(s.scenario_id): s for s in specs}
    singular_by_sid = {int(sid): g for sid, g in singular.groupby("scenario_id")}

    scenarios = []
    for sid in sorted(by_sid):
        ng = normal[normal["scenario_id"] == sid]
        sg = singular_by_sid.get(sid, normal.iloc[0:0])
        spec = by_sid[sid]
        row = {
            "sid": int(sid),
            "name": str(spec.scenario),
            "tf": str(spec.timeframe),
            "strategy": str(spec.strategy),
            "filter": str(spec.filter_name),
            "threshold": None if spec.threshold is None else float(spec.threshold),
            "blocked": int(len(ng) - len(sg)),
            "blocked_pct": float((len(ng) - len(sg)) / len(ng) * 100.0) if len(ng) else 0.0,
        }
        row.update(prefixed(ng, "n_", INITIAL))
        row.update(prefixed(sg, "s_", INITIAL))
        row["delta_retAll"] = row["s_retAll"] - row["n_retAll"]
        scenarios.append(row)

    id_count = normal.groupby("scenario")["scenario_id"].nunique().to_dict()
    by_name = []
    for name, ng in normal.groupby("scenario", sort=True):
        sg = singular[singular["scenario"] == name]
        cap = INITIAL * int(id_count.get(name, 1))
        row = {"scenario": name, "blocked": int(len(ng) - len(sg)),
               "blocked_pct": float((len(ng) - len(sg)) / len(ng) * 100.0) if len(ng) else 0.0}
        row.update(prefixed(ng, "n_", cap))
        row.update(prefixed(sg, "s_", cap))
        row["delta_retAll"] = row["s_retAll"] - row["n_retAll"]
        by_name.append(row)

    id_count_strat = normal.groupby("strategy")["scenario_id"].nunique().to_dict()
    by_strategy = []
    for strat, ng in normal.groupby("strategy", sort=True):
        sg = singular[singular["strategy"] == strat]
        cap = INITIAL * int(id_count_strat.get(strat, 1))
        row = {"strategy": strat, "blocked": int(len(ng) - len(sg)),
               "blocked_pct": float((len(ng) - len(sg)) / len(ng) * 100.0) if len(ng) else 0.0}
        row.update(prefixed(ng, "n_", cap))
        row.update(prefixed(sg, "s_", cap))
        row["delta_retAll"] = row["s_retAll"] - row["n_retAll"]
        by_strategy.append(row)

    s_groups = {key: g for key, g in singular.groupby(["scenario_id", "symbol"])}
    coins = []
    for (sid, symbol), ng in normal.groupby(["scenario_id", "symbol"], sort=True):
        sg = s_groups.get((sid, symbol), normal.iloc[0:0])
        nm = simple(ng, INITIAL)
        sm = simple(sg, INITIAL)
        coins.append({
            "sid": int(sid), "symbol": str(symbol),
            "blocked": int(len(ng) - len(sg)),
            "n_trades": nm["trades"], "n_retAll": nm["retAll"],
            "n_win_rate": nm["win_rate"], "n_avg_ret": nm["avg_ret"],
            "s_trades": sm["trades"], "s_retAll": sm["retAll"],
            "s_win_rate": sm["win_rate"], "s_avg_ret": sm["avg_ret"],
        })

    total_capital = INITIAL * int(normal["scenario_id"].nunique())

    def totals(df: pd.DataFrame) -> dict:
        m = metrics(df, total_capital)
        m["scenarios"] = int(df["scenario_id"].nunique())
        m["capital_tl"] = total_capital
        return m

    return {
        "repo": "paper-ml-bsh",
        "generated_at": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rule": ("Aynı senaryoda aynı coin için aynı anda en fazla bir açık işlem. "
                 "Sinyal, işlem açıkken gelirse bloke edilir."),
        "scope": "scenario",
        "scope_label": "Senaryo (scenario_id) + coin",
        "data": meta,
        "totals": {
            "normal": totals(normal),
            "singular": totals(singular),
            "blocked": int(len(normal) - len(singular)),
        },
        "scenarios": scenarios,
        "scenario_names": by_name,
        "strategies": by_strategy,
        "coins": coins,
    }


def write_payload(payload: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "singular_results.json").write_text(
        json.dumps(_round(payload), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    pd.DataFrame(payload["scenarios"]).to_csv(
        out / "singular_scenarios.csv", index=False)
    pd.DataFrame(payload["scenario_names"]).to_csv(
        out / "singular_scenario_names.csv", index=False)
    pd.DataFrame(payload["strategies"]).to_csv(
        out / "singular_strategies.csv", index=False)
    pd.DataFrame(payload["coins"]).to_csv(
        out / "singular_coins.csv", index=False)
