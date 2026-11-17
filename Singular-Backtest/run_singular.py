#!/usr/bin/env python3
"""Singular backtest for the packaged 219-scenario paper-trade workbook.

Rule
----
For every strategy/scenario and coin, at most one trade may be open at any
moment.  If a new signal arrives while that coin already has an open trade for
the same scenario, the signal is ignored.  This is enforced across the whole
signal stream, so stacked entries (the normal paper engine allows them) are
blocked.

The folder is separate from the normal backtest dashboard.  Inputs are read
from the packaged workbook, outputs are written under
`Singular-Backtest/results/`.

Run:
    python Singular-Backtest/run_singular.py
    python Singular-Backtest/run_singular.py --scope scenario_name
    python Singular-Backtest/run_singular.py --from-cache

Scopes
------
scenario       scenario_id + coin (default; one portfolio per dashboard row)
scenario_name  scenario name + coin (same config across timeframes)
strategy       strategy family + coin (strictest)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
WORKBOOK = (
    ROOT
    / "PAPER_TRADE_POSITIVE_MODELS_1M_EXECUTION"
    / "paper_trade_package"
    / "BACKTEST_POSITIVE_METHODS_1M_EXECUTION.xlsx"
)
SHEET = "Detayli_Islemler"
STAKE_TL = 10_000.0
INITIAL_TL = 100_000.0

SCOPE_KEYS = {
    "scenario": ["scenario_id", "symbol"],
    "scenario_name": ["scenario", "symbol"],
    "strategy": ["strategy", "symbol"],
}
SCOPE_LABEL = {
    "scenario": "Senaryo (scenario_id) + coin",
    "scenario_name": "Senaryo adı (tüm TF'ler) + coin",
    "strategy": "Strateji ailesi + coin",
}


def load_trades(workbook: Path) -> pd.DataFrame:
    df = pd.read_excel(workbook, sheet_name=SHEET)
    for col in ("signal_time", "entry_time", "exit_time"):
        df[col] = pd.to_datetime(df[col])
    df = df.dropna(subset=["signal_time", "entry_time", "exit_time",
                           "scenario_id", "symbol", "net_pnl_tl"])
    df = df[df["exit_time"] > df["entry_time"]]
    df["scenario_id"] = df["scenario_id"].astype(int)
    return df.reset_index(drop=True)


def merge_singular(cand: pd.DataFrame, scope: str) -> pd.DataFrame:
    keys = SCOPE_KEYS[scope]
    accepted: list[int] = []
    for _, group in cand.groupby(keys, sort=False, observed=True):
        group = group.sort_values(
            ["signal_time", "entry_time", "exit_time"], kind="mergesort"
        )
        last_exit = None
        for idx, row in group.iterrows():
            if last_exit is not None and row["signal_time"] < last_exit:
                continue
            accepted.append(idx)
            last_exit = row["exit_time"]
    return cand.loc[sorted(accepted)].copy()


def _max_drawdown(pnl: pd.Series, initial: float = INITIAL_TL) -> float:
    if pnl.empty:
        return 0.0
    equity = initial + pnl.cumsum()
    peak = equity.cummax()
    dd = (equity / peak - 1.0) * 100.0
    return float(dd.min())


def metrics(g: pd.DataFrame, capital_tl: float = INITIAL_TL) -> dict:
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
    g_chrono = g.sort_values("exit_time", kind="mergesort")
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
        "retAll": float(g["net_pnl_tl"].sum() / capital_tl * 100.0),
        "ret2026": float(g.loc[year2026, "net_pnl_tl"].sum() / capital_tl * 100.0),
        "winRatio": wins / losses if losses else float(wins),
        "winHoldH": float(hold_h[win].mean()) if win.any() else 0.0,
        "profitDays": int((daily > 0).sum()),
        "lossDays": int((daily < 0).sum()),
        "maxDD": _max_drawdown(g_chrono["net_pnl_tl"], capital_tl),
        "tp": int((outcome == "TP").sum()),
        "sl": int((outcome == "SL").sum()),
        "time": int((outcome == "TIME").sum()),
        "net_pnl_tl": float(g["net_pnl_tl"].sum()),
        "symbols": int(g["symbol"].nunique()),
    }


def _prefixed(g: pd.DataFrame, prefix: str, capital_tl: float) -> dict:
    return {f"{prefix}{k}": v for k, v in metrics(g, capital_tl).items()}


def aggregate(normal: pd.DataFrame, singular: pd.DataFrame, scope: str,
              meta: dict) -> dict:
    scenarios = []
    for sid, ng in normal.groupby("scenario_id", sort=True):
        sg = singular[singular["scenario_id"] == sid]
        first = ng.iloc[0]
        row = {
            "sid": int(sid),
            "name": str(first["scenario"]),
            "tf": str(first["timeframe"]),
            "strategy": str(first["strategy"]),
            "filter": str(first["filter"]),
            "threshold": None if pd.isna(first["threshold"]) else float(first["threshold"]),
            "blocked": int(len(ng) - len(sg)),
            "blocked_pct": float((len(ng) - len(sg)) / len(ng) * 100.0),
        }
        row.update(_prefixed(ng, "n_", INITIAL_TL))
        row.update(_prefixed(sg, "s_", INITIAL_TL))
        row["delta_retAll"] = row["s_retAll"] - row["n_retAll"]
        scenarios.append(row)

    # One row per scenario name, merging its timeframe variants.  The capital
    # base is 100k per variant so the percentages stay comparable.
    id_count = normal.groupby("scenario")["scenario_id"].nunique().to_dict()
    by_name = []
    for name, ng in normal.groupby("scenario", sort=True):
        sg = singular[singular["scenario"] == name]
        cap = INITIAL_TL * int(id_count.get(name, 1))
        row = {"scenario": name, "blocked": int(len(ng) - len(sg)),
               "blocked_pct": float((len(ng) - len(sg)) / len(ng) * 100.0)}
        row.update(_prefixed(ng, "n_", cap))
        row.update(_prefixed(sg, "s_", cap))
        row["delta_retAll"] = row["s_retAll"] - row["n_retAll"]
        by_name.append(row)

    strategy_rows = []
    id_count_strat = normal.groupby("strategy")["scenario_id"].nunique().to_dict()
    for strat, ng in normal.groupby("strategy", sort=True):
        sg = singular[singular["strategy"] == strat]
        cap = INITIAL_TL * id_count_strat.get(strat, 1)
        row = {"strategy": strat, "blocked": int(len(ng) - len(sg)),
               "blocked_pct": float((len(ng) - len(sg)) / len(ng) * 100.0)}
        row.update(_prefixed(ng, "n_", cap))
        row.update(_prefixed(sg, "s_", cap))
        row["delta_retAll"] = row["s_retAll"] - row["n_retAll"]
        strategy_rows.append(row)

    coins = []
    for (sid, symbol), ng in normal.groupby(["scenario_id", "symbol"], sort=True):
        sg = singular[(singular["scenario_id"] == sid) &
                      (singular["symbol"] == symbol)]
        nm = metrics(ng)
        sm = metrics(sg)
        coins.append({
            "sid": int(sid), "symbol": str(symbol),
            "blocked": int(len(ng) - len(sg)),
            "n_trades": nm["trades"], "n_retAll": nm["retAll"],
            "n_win_rate": nm["win_rate"], "n_avg_ret": nm["avg_ret"],
            "s_trades": sm["trades"], "s_retAll": sm["retAll"],
            "s_win_rate": sm["win_rate"], "s_avg_ret": sm["avg_ret"],
        })

    total_capital = INITIAL_TL * int(normal["scenario_id"].nunique())

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
        "scope": scope,
        "scope_label": SCOPE_LABEL[scope],
        "data": meta,
        "totals": {
            "normal": totals(normal),
            "singular": totals(singular),
            "blocked": int(len(normal) - len(singular)),
        },
        "scenarios": scenarios,
        "scenario_names": by_name,
        "strategies": strategy_rows,
        "coins": coins,
    }


def _round(obj, nd: int = 4):
    if isinstance(obj, float):
        return round(obj, nd)
    if isinstance(obj, dict):
        return {k: _round(v, nd) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round(v, nd) for v in obj]
    return obj


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--workbook", type=Path, default=WORKBOOK)
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parent / "results")
    ap.add_argument("--scope", choices=list(SCOPE_KEYS), default="scenario")
    ap.add_argument("--from-cache", action="store_true",
                    help="reuse results/normal_trades.parquet instead of the workbook")
    ap.add_argument("--force", action="store_true",
                    help="re-read the workbook even when a cache exists")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    cache = args.out / "normal_trades.parquet"
    if cache.exists() and not args.force:
        print(f"loading cache {cache}", flush=True)
        normal = pd.read_parquet(cache)
        for col in ("signal_time", "entry_time", "exit_time"):
            normal[col] = pd.to_datetime(normal[col])
    else:
        print(f"reading {args.workbook}", flush=True)
        normal = load_trades(args.workbook)
        normal.to_parquet(cache, index=False, compression="zstd")

    meta = {
        "symbols": int(normal["symbol"].nunique()),
        "scenarios": int(normal["scenario_id"].nunique()),
        "scenario_names": int(normal["scenario"].nunique()),
        "strategies": int(normal["strategy"].nunique()),
        "start": str(normal["entry_time"].min()),
        "end": str(normal["exit_time"].max()),
        "timeframes": sorted(normal["timeframe"].unique().tolist()),
        "source": "PAPER_TRADE_POSITIVE_MODELS_1M_EXECUTION/paper_trade_package/"
                  "BACKTEST_POSITIVE_METHODS_1M_EXECUTION.xlsx",
        "stake_tl": STAKE_TL,
        "initial_tl": INITIAL_TL,
        "timezone": "Türkiye saati (UTC+3, kaynak dosyadaki gibi)",
    }

    singular = merge_singular(normal, args.scope)
    singular.to_parquet(args.out / "singular_trades.parquet", index=False,
                        compression="zstd")
    payload = aggregate(normal, singular, args.scope, meta)

    (args.out / "singular_results.json").write_text(
        json.dumps(_round(payload), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    pd.DataFrame(payload["scenarios"]).to_csv(
        args.out / "singular_scenarios.csv", index=False)
    pd.DataFrame(payload["scenario_names"]).to_csv(
        args.out / "singular_scenario_names.csv", index=False)
    pd.DataFrame(payload["strategies"]).to_csv(
        args.out / "singular_strategies.csv", index=False)
    pd.DataFrame(payload["coins"]).to_csv(
        args.out / "singular_coins.csv", index=False)
    (args.out / "run_manifest.json").write_text(
        json.dumps(_round({
            "generated_at": payload["generated_at"],
            "scope": args.scope,
            "source_rows": int(len(normal)),
            "accepted_rows": int(len(singular)),
            "blocked_rows": int(len(normal) - len(singular)),
            "meta": meta,
        }), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    t = payload["totals"]
    print(f"scope={args.scope} | scenarios={len(payload['scenarios'])} "
          f"coins={len(payload['coins'])}", flush=True)
    print(f"normal={t['normal']['trades']:,} "
          f"singular={t['singular']['trades']:,} "
          f"blocked={t['blocked']:,} "
          f"({t['blocked'] / t['normal']['trades'] * 100:.2f}%)", flush=True)
    print(f"normal retAll={t['normal']['retAll']:+.2f}% "
          f"avg={t['normal']['avg_ret']:+.4f}% | "
          f"singular retAll={t['singular']['retAll']:+.2f}% "
          f"avg={t['singular']['avg_ret']:+.4f}%", flush=True)
    print(f"wrote {args.out / 'singular_results.json'}", flush=True)


if __name__ == "__main__":
    main()
